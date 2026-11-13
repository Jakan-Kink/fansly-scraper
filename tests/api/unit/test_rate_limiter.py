"""Tests for api/rate_limiter.py — token bucket rate limiter with adaptive backoff.

No external boundaries — pure logic with time-based state.
Patches time.sleep to avoid real delays; time.time for deterministic timing.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from api.rate_limiter import RateLimiter
from config import FanslyConfig
from tests.fixtures.utils import scaled_async_sleep, scaled_sync_sleep


def _make_config(**overrides: bool | int | float) -> FanslyConfig:
    """Build a real FanslyConfig with rate limiter defaults."""
    config = FanslyConfig(program_version="0.13.0-test")
    config.rate_limiting_enabled = True
    config.rate_limiting_adaptive = True
    config.rate_limiting_requests_per_minute = 60
    config.rate_limiting_burst_size = 10
    config.rate_limiting_retry_after_seconds = 30
    config.rate_limiting_backoff_factor = 1.5
    config.rate_limiting_max_backoff_seconds = 300
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestRateLimiterInit:
    """Lines 26-85: initialization, enabled/disabled logging."""

    def test_enabled(self):
        rl = RateLimiter(_make_config())
        assert rl.enabled is True
        assert rl.tokens == 10.0
        assert rl.rate_per_second == 1.0

    def test_disabled(self):
        rl = RateLimiter(_make_config(rate_limiting_enabled=False))
        assert rl.enabled is False

    def test_zero_rate(self):
        """Line 48: rate_per_second == 0 → token_refill_interval = 1.0."""
        rl = RateLimiter(_make_config(rate_limiting_requests_per_minute=0))
        assert rl.token_refill_interval == 1.0


class TestAdaptiveBackoff:
    """Lines 164-256: _apply_adaptive_backoff with 429s and success recovery."""

    def test_429_triggers_backoff_and_consecutive_successes_reduce(self):
        """Lines 169-256: 429 → backoff, then 2 successes → reduce backoff.

        Exercises: 429 path (170-191), success with backoff active (194-256),
        first success (250-256), second success → reduction (202-249).
        """
        rl = RateLimiter(_make_config())
        rl.tokens = 5.0  # nonzero so the drain assertion is meaningful

        # 429 → backoff activated, bucket drained, refill paused
        rl._apply_adaptive_backoff(429)
        assert rl.consecutive_violations == 1
        assert rl.current_backoff_seconds == 30.0  # retry_after_seconds
        assert rl.rate_limit_violations == 1
        assert rl.tokens == 0.0
        assert rl.last_refill > time.time()  # last_refill is in the future

        # Second 429 → exponential backoff
        rl._apply_adaptive_backoff(429)
        assert rl.consecutive_violations == 2
        assert rl.current_backoff_seconds == 45.0  # 30 * 1.5

        # First success → logged but not yet reduced (need 2)
        rl._apply_adaptive_backoff(200)
        assert rl.consecutive_successes == 1
        assert rl.current_backoff_seconds == 45.0  # unchanged

        # Second success → reduction triggers
        rl._apply_adaptive_backoff(200)
        assert rl.consecutive_successes == 0  # reset after reduction
        assert rl.current_backoff_seconds < 45.0

    def test_backoff_reduction_pulls_back_token_freeze(self):
        """Bug B regression: reducing backoff must also pull back the token freeze.

        On a 429 the bucket is frozen by pushing ``last_refill`` into the future
        (``last_backoff_time + current_backoff_seconds``); ``_refill_tokens`` then
        adds nothing while ``now < last_refill``. When later successes *reduce*
        ``current_backoff_seconds`` (the reduce path, line ~247), the reservation
        side shrinks (and ``_next_allowed_at`` is clamped, line ~223) but
        ``last_refill`` is never re-synced — so the freeze outlives the backoff.
        The reservation side then says "go" while the bucket stays empty, and
        ``async_wait_for_request`` livelocks in the token loop emitting
        ``Token wait`` every refill interval (observed in the field as ~45s of spam
        before a single request fired).

        Invariant: after any downward backoff adjustment, the remaining freeze
        window must not exceed the current backoff window.
        """
        rl = RateLimiter(_make_config())
        rl.learned_floor = 2.0  # floor > 0 → reduce path (not full clear)

        rl._apply_adaptive_backoff(429)  # backoff=30s; last_refill pushed +30s
        assert rl.last_refill > time.time()  # bucket frozen into the future

        rl._apply_adaptive_backoff(200)  # 1/2 successes
        rl._apply_adaptive_backoff(200)  # 2/2 → backoff reduced 30s → 20s

        freeze_window = rl.last_refill - rl.last_backoff_time
        assert freeze_window <= rl.current_backoff_seconds + 0.5, (
            f"token freeze spans {freeze_window:.1f}s but backoff is only "
            f"{rl.current_backoff_seconds:.1f}s — last_refill was not pulled back "
            "when backoff was reduced (Bug B: token-loop livelock)."
        )

    def test_backoff_at_minimum_floor(self):
        """Lines 225-226: backoff at minimum floor after consecutive successes.

        When backoff equals the floor and 2 successes arrive, the backoff
        stays at the floor (can't go lower) and successes reset.
        """
        rl = RateLimiter(_make_config())
        rl.learned_floor = 2.0
        rl._apply_adaptive_backoff(429)
        rl.current_backoff_seconds = 2.0  # already down at the floor

        rl._apply_adaptive_backoff(200)
        rl._apply_adaptive_backoff(200)

        assert rl.current_backoff_seconds == 2.0  # can't go below the floor
        assert rl.consecutive_successes == 0

    def test_backoff_normal_reduction_above_floor(self):
        """Lines 243-244: normal backoff reduction above minimum floor.

        When backoff is above the floor (but floor > 0), 2 successes reduce
        backoff but not below the floor.
        """
        rl = RateLimiter(_make_config())
        rl.learned_floor = 2.0
        rl._apply_adaptive_backoff(429)  # 30s backoff, well above the floor

        rl._apply_adaptive_backoff(200)
        rl._apply_adaptive_backoff(200)

        assert rl.current_backoff_seconds == pytest.approx(20.0)  # 30 / 1.5
        assert rl.consecutive_successes == 0

    def test_429_at_max_backoff(self):
        """Lines 184-191: consecutive 429s hit max_backoff_seconds cap."""
        rl = RateLimiter(
            _make_config(
                rate_limiting_retry_after_seconds=100,
                rate_limiting_backoff_factor=10.0,
                rate_limiting_max_backoff_seconds=300,
            )
        )

        rl._apply_adaptive_backoff(429)
        rl._apply_adaptive_backoff(429)  # 100 * 10 = 1000 → capped at 300
        assert rl.current_backoff_seconds == 300.0

    def test_adaptive_disabled_noop(self):
        """Line 166-167: adaptive=False → no backoff applied."""
        rl = RateLimiter(_make_config(rate_limiting_adaptive=False))
        rl._apply_adaptive_backoff(429)
        assert rl.current_backoff_seconds == 0.0

    def test_backoff_reduced_to_zero_when_no_floor(self):
        """Lines 232-240: minimum_floor ≤ 0 → backoff fully resets to 0."""
        rl = RateLimiter(
            _make_config(
                rate_limiting_retry_after_seconds=1,
                rate_limiting_backoff_factor=1.1,
            )
        )
        # Trigger a small backoff
        rl._apply_adaptive_backoff(429)
        assert rl.current_backoff_seconds == 1.0

        # Fill outcomes with successes so learned_floor stays 0
        rl.request_outcomes.clear()

        # 2 successes → reduce → floor is 0 → full reset
        rl._apply_adaptive_backoff(200)
        rl._apply_adaptive_backoff(200)
        assert rl.current_backoff_seconds == 0.0
        assert rl.consecutive_violations == 0


class TestMinimumFloor:
    """The learned floor: raised by rate-limit responses, lowered by success runs."""

    def test_floor_rises_per_rate_limit_response_and_steps_down(self):
        """A rate-limit response raises the floor once; each full run of
        successes divides it by the backoff factor until it is gone.

        The raise is not repeated while that response is still in the window,
        so a step down is never undone. A new rate-limit response raises the
        floor again and restarts the count. With no 429 on record the floor
        can fall to zero.
        """
        rl = RateLimiter(
            _make_config(
                rate_limiting_success_requests=5, rate_limiting_backoff_factor=2.0
            )
        )

        def respond(status: int, times: int = 1) -> float:
            for _ in range(times):
                rl.record_response(status, 0.1)
            return rl._calculate_minimum_floor()

        assert respond(200, 19) == 0.0
        # 1 failure in 20 requests: 1 + 0.05 * 20 * 2.0
        assert respond(503) == pytest.approx(3.0)
        # Four successes are not a full run; the fifth is. The 503 is still in
        # the window and does not raise the floor back.
        assert respond(200, 4) == pytest.approx(3.0)
        assert respond(200) == pytest.approx(1.5)
        # 2 failures in 30 requests: 1 + (2 / 30) * 40. The count restarts.
        assert respond(200, 4) == pytest.approx(1.5)
        assert respond(503) == pytest.approx(11 / 3)
        assert respond(200, 4) == pytest.approx(11 / 3)
        assert respond(200) == pytest.approx(11 / 6)
        # Halved per run of five, then dropped to zero once under 0.1s.
        assert [respond(200, 5) for _ in range(5)] == pytest.approx(
            [11 / 12, 11 / 24, 11 / 48, 11 / 96, 0.0]
        )
        assert respond(200, 5) == 0.0

    def test_floor_stops_at_half_a_second_after_a_429(self):
        """Once there has been a 429 the floor is lowered to 0.5s and no further."""
        rl = RateLimiter(
            _make_config(
                rate_limiting_success_requests=2, rate_limiting_backoff_factor=2.0
            )
        )
        rl.learned_floor = 1.6
        rl.rate_limit_violations = 1

        floors = []
        for _ in range(3):
            rl.record_response(200, 0.1)
            rl.record_response(200, 0.1)
            floors.append(rl.learned_floor)

        assert floors == [0.8, 0.5, 0.5]

    def test_floor_raise_waits_for_ten_requests(self):
        """A rate-limit response among the first requests raises the floor
        only once ten requests are on record."""
        rl = RateLimiter(_make_config())
        rl.record_response(503, 0.1)
        for _ in range(8):
            rl.record_response(200, 0.1)
        assert rl._calculate_minimum_floor() == 0.0

        rl.record_response(200, 0.1)
        # 1 failure in 10 requests: 1 + 0.1 * 20 * 1.5
        assert rl._calculate_minimum_floor() == pytest.approx(4.0)

        # A rate-limit response never lowers a floor that is already higher.
        rl.learned_floor = 10.0
        rl.record_response(503, 0.1)
        assert rl._calculate_minimum_floor() == 10.0


class TestWaitForRequest:
    """Lines 258-336, 338-413: sync and async wait paths."""

    def test_disabled_returns_zero(self):
        """Lines 269-270, 345-346: disabled → immediate return."""
        rl = RateLimiter(_make_config(rate_limiting_enabled=False))
        assert rl.wait_for_request() == 0.0

    @pytest.mark.asyncio
    async def test_async_disabled_returns_zero(self):
        rl = RateLimiter(_make_config(rate_limiting_enabled=False))
        assert await rl.async_wait_for_request() == 0.0

    def test_sync_wait_consumes_token(self):
        """Lines 272-336: normal request → consumes token, records time."""
        rl = RateLimiter(_make_config())
        initial_tokens = rl.tokens
        rl.wait_for_request()
        assert rl.tokens == initial_tokens - 1.0
        assert rl.total_requests == 1
        assert len(rl.request_history) == 1

    @pytest.mark.asyncio
    async def test_async_wait_consumes_token(self):
        """Lines 338-413: async version consumes token."""
        rl = RateLimiter(_make_config())
        initial_tokens = rl.tokens
        await rl.async_wait_for_request()
        assert rl.tokens == initial_tokens - 1.0

    def test_sync_wait_with_backoff(self):
        """Lines 290-296, 312-314: in backoff → sleeps backoff_remaining."""
        rl = RateLimiter(_make_config())
        rl.current_backoff_seconds = 1.0
        rl.last_backoff_time = rl.last_refill  # set to now-ish

        with patch("api.rate_limiter.time.sleep", scaled_sync_sleep):
            rl.wait_for_request()

        assert rl.blocked_requests == 1

    def test_sync_wait_with_token_depletion(self):
        """Lines 300-302, 316-321: no tokens → sleeps refill interval."""
        rl = RateLimiter(_make_config())
        rl.tokens = 0.0

        with patch("api.rate_limiter.time.sleep", scaled_sync_sleep):
            rl.wait_for_request()

        assert rl.blocked_requests == 1

    @pytest.mark.asyncio
    async def test_async_wait_with_backoff_and_token_depletion(self):
        """Lines 366-402: async paths for backoff + token wait."""
        rl = RateLimiter(_make_config())
        rl.current_backoff_seconds = 0.01
        rl.last_backoff_time = rl.last_refill
        rl.tokens = 0.0

        await rl.async_wait_for_request()
        assert rl.blocked_requests >= 1

    def test_sync_wait_with_floor_enforcement(self):
        """Lines 283-287, 305-310: minimum floor enforces delay between requests."""
        rl = RateLimiter(_make_config())
        rl.learned_floor = 0.01
        rl.last_request_time = rl.last_refill  # recent

        with patch("api.rate_limiter.time.sleep", scaled_sync_sleep):
            rl.wait_for_request()

    @pytest.mark.asyncio
    async def test_async_wait_with_floor_enforcement(self):
        """Lines 361-363, 388-392: async minimum floor enforces delay."""
        rl = RateLimiter(_make_config())
        rl.learned_floor = 0.01
        rl.last_request_time = rl.last_refill  # recent request

        await rl.async_wait_for_request()

    @pytest.mark.asyncio
    async def test_concurrent_async_waiters_serialize_at_floor_zero(self):
        """Bug A regression guard: concurrent waiters serialize even when
        learned_floor==0 (cold start). Without _next_allowed_at advancing by
        token_refill_interval as a minimum, all waiters reserve target==now
        and fire simultaneously — the original 429-flood pattern.
        """
        rl = RateLimiter(_make_config())  # learned_floor starts at 0
        rl.tokens = 100.0  # ensure token loop never blocks
        assert rl.learned_floor == 0.0
        step = rl.token_refill_interval  # 1.0s for 60 rpm

        with patch("api.rate_limiter.asyncio.sleep", new=scaled_async_sleep):
            await asyncio.gather(*[rl.async_wait_for_request() for _ in range(5)])

        # After 5 reservations, cursor should be ~5*step ahead of the start.
        # Allow small slack for the time.time() drift across reservations.
        elapsed_cursor = rl._next_allowed_at - time.time()
        assert elapsed_cursor >= 4.5 * step, (
            f"cursor advanced by {elapsed_cursor:.2f}s; expected ~{5 * step:.2f}s. "
            "Cursor not advancing means concurrent waiters all reserved the same slot."
        )

    @pytest.mark.asyncio
    async def test_concurrent_async_waiters_serialize_with_floor(self):
        """Bug A primary case: with learned_floor>0, cursor advances by floor."""
        rl = RateLimiter(_make_config())
        rl.learned_floor = 2.0
        rl.tokens = 100.0
        rl.last_request_time = time.time()  # ensures floor is applied

        with patch("api.rate_limiter.asyncio.sleep", new=scaled_async_sleep):
            await asyncio.gather(*[rl.async_wait_for_request() for _ in range(3)])

        # 3 callers, each spaced by max(floor=2.0, refill=1.0) = 2.0s
        elapsed_cursor = rl._next_allowed_at - time.time()
        assert elapsed_cursor >= 5.5, (
            f"cursor at {elapsed_cursor:.2f}s; expected ~6.0s for 3 waiters at 2.0s spacing."
        )


class TestRecordResponse:
    """Lines 415-451: record_response feeds adaptive backoff + outcomes."""

    def test_disabled_noop(self):
        """Line 423-424: disabled → return."""
        rl = RateLimiter(_make_config(rate_limiting_enabled=False))
        rl.record_response(429, 0.1)
        assert rl.rate_limit_violations == 0

    def test_429_recorded(self):
        """Lines 426-447: 429 → outcome=False, adaptive backoff triggered."""
        rl = RateLimiter(_make_config())
        rl.record_response(429, 0.1)
        assert rl.rate_limit_violations == 1
        assert rl.request_outcomes[-1] is False

    def test_200_recorded(self):
        """200 → outcome=True."""
        rl = RateLimiter(_make_config())
        rl.record_response(200, 0.1)
        assert rl.request_outcomes[-1] is True

    def test_slow_response_logged(self):
        """Lines 450-451: response_time > 5s → debug log."""
        rl = RateLimiter(_make_config())
        rl.record_response(200, 6.0)  # just verify no crash

    def test_client_error_is_success_outcome(self):
        """Lines 442-444: 400 is not in rate_limit_errors → outcome=True."""
        rl = RateLimiter(_make_config())
        rl.record_response(400, 0.1)
        assert rl.request_outcomes[-1] is True

    def test_rate_limit_error_codes_are_failure_outcomes(self):
        """Lines 430-444: all rate_limit_errors → outcome=False."""
        rl = RateLimiter(_make_config())
        for code in (408, 418, 425, 429, 500, 502, 503, 504):
            rl.record_response(code, 0.1)
            assert rl.request_outcomes[-1] is False, f"status {code} should be failure"


class TestStatsAndConfig:
    """Lines 453-549: get_stats, reset_stats, update_config."""

    def test_get_stats(self):
        """Lines 453-497: returns comprehensive stats dict."""
        rl = RateLimiter(_make_config())
        rl.wait_for_request()
        stats = rl.get_stats()
        assert stats["enabled"] is True
        assert stats["total_requests"] == 1
        assert stats["available_tokens"] == 9.0
        assert "utilization_percent" in stats

        # With active backoff → hits lines 474-475 (backoff remaining calculation)
        rl._apply_adaptive_backoff(429)
        stats_with_backoff = rl.get_stats()
        assert stats_with_backoff["backoff_remaining"] >= 0.0
        assert stats_with_backoff["rate_limit_violations"] == 1

    def test_reset_stats(self):
        """Lines 499-507: resets counters."""
        rl = RateLimiter(_make_config())
        rl.wait_for_request()
        rl.record_response(429, 0.1)
        rl.reset_stats()
        assert rl.total_requests == 0
        assert rl.blocked_requests == 0
        assert rl.rate_limit_violations == 0
        assert len(rl.request_history) == 0

    def test_update_config(self):
        """Lines 509-549: updates config, recalculates, logs changes."""
        rl = RateLimiter(_make_config())

        # Change rate
        assert rl.success_requests == 250
        new_config = _make_config(
            rate_limiting_requests_per_minute=120, rate_limiting_success_requests=40
        )
        rl.update_config(new_config)
        assert rl.requests_per_minute == 120
        assert rl.rate_per_second == 2.0
        assert rl.success_requests == 40

        # Disable
        disabled_config = _make_config(rate_limiting_enabled=False)
        rl.update_config(disabled_config)
        assert rl.enabled is False

        # Shrink burst (tokens should be capped)
        rl.tokens = 100.0
        small_burst = _make_config(rate_limiting_burst_size=5)
        rl.update_config(small_burst)
        assert rl.tokens == 5.0

        # The same config again changes nothing
        rl.update_config(small_burst)
        assert rl.tokens == 5.0
        assert rl.enabled is True
