"""
Rate limiting implementation for API requests.
"""

import asyncio
import time
from collections import deque
from threading import Lock
from typing import TYPE_CHECKING, Any

from config.logging import textio_logger as logger

if TYPE_CHECKING:
    from config import FanslyConfig


class RateLimiter:
    """
    Token bucket rate limiter with adaptive backoff support.

    This implementation uses a token bucket algorithm to control request rates,
    with support for burst traffic and adaptive rate limiting based on server responses.
    """

    def __init__(self, config: "FanslyConfig"):
        """
        Initialize the rate limiter.

        Args:
            config: Configuration instance containing rate limiting settings
        """
        self.config = config
        self._lock = Lock()

        # Load configuration from FanslyConfig (defaults defined in config dataclass)
        self.enabled = config.rate_limiting_enabled
        self.requests_per_minute = config.rate_limiting_requests_per_minute
        self.burst_size = config.rate_limiting_burst_size
        self.retry_after_seconds = config.rate_limiting_retry_after_seconds
        self.adaptive_rate_limiting = config.rate_limiting_adaptive
        self.backoff_factor = config.rate_limiting_backoff_factor
        self.max_backoff_seconds = config.rate_limiting_max_backoff_seconds

        # Calculate rate parameters
        self.rate_per_second = self.requests_per_minute / 60.0
        self.token_refill_interval = (
            1.0 / self.rate_per_second if self.rate_per_second > 0 else 1.0
        )

        # Token bucket state
        self.tokens = float(self.burst_size)
        self.last_refill = time.time()

        # Request tracking for adaptive rate limiting
        self.request_history: deque[float] = deque(maxlen=100)
        self.last_request_time: float = (
            0.0  # Track last request for minimum floor enforcement
        )
        # Track violations in sliding window for adaptive floor calculation
        self.request_outcomes: deque[bool] = deque(
            maxlen=1000
        )  # True = success, False = 429
        self.rate_limit_violations = 0  # Total lifetime violations (for logging)
        self.consecutive_violations = 0
        self.consecutive_successes = 0  # Track successful requests after rate limiting
        self.current_backoff_seconds = 0.0
        self.last_backoff_time = 0.0

        # Learned persistent floor: ramps up quickly on 429s, decays slowly with sustained success
        self.learned_floor = 0.0

        # Statistics
        self.total_requests = 0
        self.blocked_requests = 0
        self.adaptive_adjustments = 0

        if self.enabled:
            logger.info(
                f"Rate limiter initialized: {self.requests_per_minute} "
                f"requests/minute, burst size: {self.burst_size}, "
                f"adaptive: {self.adaptive_rate_limiting}"
            )
        else:
            logger.info("Rate limiting is disabled")

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill

        if elapsed > 0:
            # Calculate tokens to add
            tokens_to_add = elapsed * self.rate_per_second
            self.tokens = min(self.burst_size, self.tokens + tokens_to_add)
            self.last_refill = now

    def _is_in_backoff(self) -> bool:
        """Check if we're currently in a backoff period."""
        if self.current_backoff_seconds <= 0:
            return False

        elapsed = time.time() - self.last_backoff_time
        return elapsed < self.current_backoff_seconds

    def _calculate_minimum_floor(self) -> float:
        """Calculate the minimum delay between requests based on learned behavior.

        The learned floor:
        - Ramps up QUICKLY when we get 429s (even while respecting current floor)
        - Decays SLOWLY only after proving 1000 consecutive successes
        - Never resets to 0 once we've learned we need rate limiting

        This ensures the floor adapts to API behavior without oscillation.
        """
        # Need at least 10 requests in history to calculate a meaningful rate
        if len(self.request_outcomes) < 10:
            return self.learned_floor

        # Count recent violations (False = 429)
        recent_violations = sum(1 for outcome in self.request_outcomes if not outcome)

        if recent_violations > 0:
            # We're getting 429s - floor is too low, increase it quickly
            violation_rate = recent_violations / len(self.request_outcomes)

            # Calculate what floor should be based on violation rate
            # Use aggressive multiplier for quick ramp-up
            rate_multiplier = 20.0 * self.backoff_factor
            calculated_floor = 1.0 + (violation_rate * rate_multiplier)
            calculated_floor = min(20.0, calculated_floor)  # Cap at 20s

            # Increase learned floor immediately (ramp up quickly)
            if calculated_floor > self.learned_floor:
                old_floor = self.learned_floor
                self.learned_floor = calculated_floor
                logger.info(
                    f"Floor increased: {old_floor:.1f}s → {self.learned_floor:.1f}s "
                    f"(violation rate: {violation_rate * 100:.1f}%)"
                )

        elif len(self.request_outcomes) == 1000:
            # Full window of success! Floor is working, can slowly decrease
            if self.learned_floor > 0:
                old_floor = self.learned_floor

                # Decay by 5% but never below 0.5s if we've ever had violations
                if self.rate_limit_violations > 0:
                    self.learned_floor = max(0.5, self.learned_floor * 0.95)
                else:
                    # No lifetime violations - can go to 0
                    self.learned_floor = self.learned_floor * 0.95
                    if self.learned_floor < 0.1:
                        self.learned_floor = 0.0

                if old_floor != self.learned_floor:
                    logger.info(
                        f"Floor decayed after 1000 successes: {old_floor:.1f}s → {self.learned_floor:.1f}s"
                    )

        # Return current learned floor (stable between adjustments)
        return self.learned_floor

    def _apply_adaptive_backoff(self, status_code: int) -> None:
        """Apply adaptive backoff based on server response."""
        if not self.adaptive_rate_limiting:
            return

        if status_code == 429:  # Too Many Requests
            self.rate_limit_violations += 1
            self.consecutive_violations += 1
            self.consecutive_successes = 0  # Reset success counter on rate limit

            # Calculate exponential backoff: base * (factor ^ consecutive_violations)
            # Cap at max_backoff_seconds
            backoff = self.retry_after_seconds * (
                self.backoff_factor ** (self.consecutive_violations - 1)
            )
            self.current_backoff_seconds = min(backoff, self.max_backoff_seconds)
            self.last_backoff_time = time.time()
            self.adaptive_adjustments += 1

            # Log with additional context about consecutive violations
            at_max = self.current_backoff_seconds >= self.max_backoff_seconds
            logger.warning(
                f"Rate limit violation detected (429). "
                f"Consecutive violations: {self.consecutive_violations}, "
                f"applying {self.current_backoff_seconds:.1f}s backoff"
                f"{' (MAX)' if at_max else ''}. "
                f"Total violations: {self.rate_limit_violations}"
            )
        elif 200 <= status_code < 300:
            # Successful response - handle backoff reduction while any backoff is active
            if self.current_backoff_seconds > 0:
                self.consecutive_successes += 1

                # Roll forward the backoff window to maintain interval between requests
                # This ensures we continue enforcing backoff for subsequent requests
                self.last_backoff_time = time.time()

                # Only reduce backoff after 2 consecutive successful requests
                if self.consecutive_successes >= 2:
                    old_backoff = self.current_backoff_seconds

                    # Calculate adaptive minimum backoff based on violation rate
                    minimum_backoff = self._calculate_minimum_floor()

                    # Reduce backoff but never below the minimum
                    reduced_backoff = self.current_backoff_seconds / self.backoff_factor
                    self.current_backoff_seconds = max(minimum_backoff, reduced_backoff)

                    # Reset backoff timer to enforce the new (reduced) backoff duration
                    self.last_backoff_time = time.time()

                    self.consecutive_violations = max(
                        0, self.consecutive_violations - 1
                    )

                    # Check if we've reached the minimum threshold
                    if (
                        self.current_backoff_seconds <= minimum_backoff
                        and minimum_backoff > 0.0
                    ):
                        # At minimum floor
                        self.consecutive_successes = 0
                        logger.info(
                            f"2 consecutive successes - backoff at minimum floor: "
                            f"{self.current_backoff_seconds:.1f}s "
                            f"(would be {reduced_backoff:.1f}s without floor). "
                            f"Total violations: {self.rate_limit_violations}"
                        )
                    elif minimum_backoff <= 0.0:
                        # Can fully reset
                        self.current_backoff_seconds = 0.0
                        self.consecutive_violations = 0
                        self.consecutive_successes = 0
                        logger.info(
                            f"Backoff reduced from {old_backoff:.1f}s to 0s after "
                            "2 consecutive successful requests. Rate limiting normalized."
                        )
                    else:
                        # Normal reduction above minimum
                        self.consecutive_successes = 0
                        logger.info(
                            f"2 consecutive successes - backoff reduced from "
                            f"{old_backoff:.1f}s to {self.current_backoff_seconds:.1f}s "
                            f"(minimum floor: {minimum_backoff:.1f}s). "
                            f"Total violations: {self.rate_limit_violations}"
                        )
                else:
                    # First successful request after rate limiting
                    logger.info(
                        f"Successful request after rate limiting "
                        f"({self.consecutive_successes}/2 needed to reduce backoff). "
                        f"Current backoff: {self.current_backoff_seconds:.1f}s"
                    )

    def wait_for_request(self) -> float:
        """
        Wait for permission to make a request (synchronous version).

        Returns:
            float: Time waited in seconds
        """
        if not self.enabled:
            return 0.0

        start_time = time.time()

        with self._lock:
            self.total_requests += 1

            # Enforce minimum floor FIRST, before any other waits
            minimum_floor = self._calculate_minimum_floor()
            if minimum_floor > 0 and self.last_request_time > 0:
                time_since_last = time.time() - self.last_request_time
                if time_since_last < minimum_floor:
                    floor_wait = minimum_floor - time_since_last
                    logger.debug(
                        f"Enforcing minimum floor: waiting {floor_wait:.1f}s "
                        f"(floor: {minimum_floor:.1f}s, elapsed: {time_since_last:.1f}s)"
                    )
                    time.sleep(floor_wait)

            # Check if we're in a backoff period
            if self._is_in_backoff():
                backoff_remaining = self.current_backoff_seconds - (
                    time.time() - self.last_backoff_time
                )
                if backoff_remaining > 0:
                    self.blocked_requests += 1
                    logger.debug(f"Backing off for {backoff_remaining:.1f}s")
                    time.sleep(backoff_remaining)

            # Refill tokens
            self._refill_tokens()

            # Check if we have tokens available
            if self.tokens < 1.0:
                # Calculate wait time for next token
                wait_time = self.token_refill_interval
                self.blocked_requests += 1
                logger.debug(
                    f"Rate limit reached, waiting {wait_time:.1f}s for next token"
                )
                time.sleep(wait_time)
                self._refill_tokens()

            # Consume a token
            self.tokens -= 1.0

            # Track request timing
            current_time = time.time()
            self.request_history.append(current_time)
            self.last_request_time = current_time

        total_wait_time = time.time() - start_time
        return total_wait_time

    async def async_wait_for_request(self) -> float:
        """
        Wait for permission to make a request (async version).

        Returns:
            float: Time waited in seconds
        """
        if not self.enabled:
            return 0.0

        start_time = time.time()

        # Calculate wait times in lock, sleep outside
        floor_wait = 0.0
        backoff_remaining = 0.0
        needs_token_wait = False

        with self._lock:
            self.total_requests += 1

            # Check minimum floor enforcement FIRST
            minimum_floor = self._calculate_minimum_floor()
            if minimum_floor > 0 and self.last_request_time > 0:
                time_since_last = time.time() - self.last_request_time
                if time_since_last < minimum_floor:
                    floor_wait = minimum_floor - time_since_last

            # Check if we're in a backoff period
            if self._is_in_backoff():
                backoff_remaining = self.current_backoff_seconds - (
                    time.time() - self.last_backoff_time
                )
                if backoff_remaining > 0:
                    self.blocked_requests += 1
                    logger.debug(f"Backing off for {backoff_remaining:.1f}s")

            # Refill tokens
            self._refill_tokens()

            # Check if we have tokens available
            if self.tokens < 1.0:
                needs_token_wait = True
                self.blocked_requests += 1
                logger.debug(
                    f"Rate limit reached, waiting "
                    f"{self.token_refill_interval:.1f}s for next token"
                )

        # Async sleep for minimum floor enforcement FIRST
        if floor_wait > 0:
            logger.debug(
                f"Enforcing minimum floor: waiting {floor_wait:.1f}s "
                f"(floor: {minimum_floor:.1f}s)"
            )
            await asyncio.sleep(floor_wait)

        # Async sleep for backoff outside the lock
        if backoff_remaining > 0:
            await asyncio.sleep(backoff_remaining)

        # Async sleep for token wait outside the lock
        if needs_token_wait:
            await asyncio.sleep(self.token_refill_interval)
            with self._lock:
                self._refill_tokens()

        # Consume token and track timing
        with self._lock:
            if self.tokens >= 1.0:
                self.tokens -= 1.0
            current_time = time.time()
            self.request_history.append(current_time)
            self.last_request_time = current_time

        total_wait_time = time.time() - start_time
        return total_wait_time

    def record_response(self, status_code: int, response_time: float) -> None:
        """
        Record a response for adaptive rate limiting.

        Args:
            status_code: HTTP status code of the response
            response_time: Time taken for the request in seconds
        """
        if not self.enabled:
            return

        with self._lock:
            # Record outcome in sliding window for floor calculation
            # Only rate-limit-related errors should increase floor (slowing down helps)
            # Client errors (400-417 except 408/418) don't benefit from backing off
            rate_limit_errors = {
                408,  # Request Timeout (server too slow)
                418,  # I'm a teapot (something very wrong, RFC 2324/9110)
                425,  # Too Early (server not ready)
                429,  # Too Many Requests (explicit rate limit)
                500,  # Internal Server Error (server struggling)
                502,  # Bad Gateway (upstream overloaded)
                503,  # Service Unavailable (server overloaded)
                504,  # Gateway Timeout (upstream too slow)
            }

            # True = success, False = rate limit issue (should increase floor)
            is_success = (200 <= status_code < 300) or (
                status_code not in rate_limit_errors
            )
            self.request_outcomes.append(is_success)

            self._apply_adaptive_backoff(status_code)

            # Log slow responses
            if response_time > 5.0:
                logger.debug(f"Slow response detected: {response_time:.2f}s")

    def get_stats(self) -> dict[str, Any]:
        """
        Get rate limiter statistics.

        Returns:
            dict: Statistics about rate limiter performance
        """
        with self._lock:
            # Calculate recent request rate
            now = time.time()
            recent_requests = [
                req_time
                for req_time in self.request_history
                if now - req_time < 60.0  # Last minute
            ]

            current_rate = len(recent_requests)

            # Calculate backoff remaining time
            backoff_remaining = 0.0
            if self._is_in_backoff():
                elapsed = now - self.last_backoff_time
                backoff_remaining = max(0.0, self.current_backoff_seconds - elapsed)

            return {
                "enabled": self.enabled,
                "configured_rate": self.requests_per_minute,
                "current_rate_per_minute": current_rate,
                "burst_size": self.burst_size,
                "available_tokens": self.tokens,
                "total_requests": self.total_requests,
                "blocked_requests": self.blocked_requests,
                "rate_limit_violations": self.rate_limit_violations,
                "consecutive_violations": self.consecutive_violations,
                "consecutive_successes": self.consecutive_successes,
                "adaptive_adjustments": self.adaptive_adjustments,
                "current_backoff_seconds": self.current_backoff_seconds,
                "backoff_remaining": backoff_remaining,
                "is_in_backoff": self._is_in_backoff(),
                "utilization_percent": (
                    (current_rate / self.requests_per_minute) * 100
                    if self.requests_per_minute > 0
                    else 0
                ),
            }

    def reset_stats(self) -> None:
        """Reset rate limiter statistics."""
        with self._lock:
            self.total_requests = 0
            self.blocked_requests = 0
            self.rate_limit_violations = 0
            self.adaptive_adjustments = 0
            self.request_history.clear()
            logger.info("Rate limiter statistics reset")

    def update_config(self, config: "FanslyConfig") -> None:
        """
        Update rate limiter configuration.

        Args:
            config: New configuration instance
        """
        with self._lock:
            old_enabled = self.enabled
            old_rate = self.requests_per_minute

            # Update configuration from FanslyConfig (defaults defined in config dataclass)
            self.config = config
            self.enabled = config.rate_limiting_enabled
            self.requests_per_minute = config.rate_limiting_requests_per_minute
            self.burst_size = config.rate_limiting_burst_size
            self.retry_after_seconds = config.rate_limiting_retry_after_seconds
            self.adaptive_rate_limiting = config.rate_limiting_adaptive
            self.backoff_factor = config.rate_limiting_backoff_factor
            self.max_backoff_seconds = config.rate_limiting_max_backoff_seconds

            # Recalculate rate parameters
            self.rate_per_second = self.requests_per_minute / 60.0
            self.token_refill_interval = (
                1.0 / self.rate_per_second if self.rate_per_second > 0 else 1.0
            )

            # Reset tokens if burst size changed
            if self.tokens > self.burst_size:
                self.tokens = float(self.burst_size)

            # Log changes
            if old_enabled != self.enabled:
                logger.info(
                    f"Rate limiting {'enabled' if self.enabled else 'disabled'}"
                )
            elif old_rate != self.requests_per_minute:
                logger.info(
                    f"Rate limit updated: {old_rate} -> "
                    f"{self.requests_per_minute} requests/minute"
                )
