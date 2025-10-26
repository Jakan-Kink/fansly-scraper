"""Fansly Web API"""

import binascii
import math
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
from httpx_retries import Retry, RetryTransport

from api.websocket import FanslyWebSocket
from config.logging import textio_logger as logger
from helpers.web import get_flat_qs_dict, split_url


if TYPE_CHECKING:
    from api.rate_limiter import RateLimiter


class FanslyApi:
    def __init__(
        self,
        token: str,
        user_agent: str,
        check_key: str,
        # session_id: str,
        device_id: str | None = None,
        device_id_timestamp: int | None = None,
        on_device_updated: Callable[[], Any] | None = None,
        rate_limiter: "RateLimiter | None" = None,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter

        # Define HTTP client with retry transport and HTTP/2 support
        # Create retry configuration with exponential backoff
        # httpx-retries Retry parameters:
        # - total: max retry attempts (default 10)
        # - backoff_factor: exponential backoff multiplier (default 0.0)
        # - status_forcelist: HTTP status codes to retry (default [429, 502, 503, 504])
        # - allowed_methods: HTTP methods that can be retried (default HEAD, GET, PUT, DELETE, OPTIONS, TRACE)
        # NOTE: When rate_limiter is provided, 429 is removed from status_forcelist
        # because rate limiting is handled by the RateLimiter class
        retry = Retry(
            total=3,
            backoff_factor=0.5,  # 0.5s base delay with exponential backoff: 0.5s, 1s, 2s
            status_forcelist=(
                [429, 500, 502, 503, 504]
                if rate_limiter is None
                else [500, 502, 503, 504]
            ),
        )

        # Create transport with HTTP/2 and retry support
        base_transport = httpx.HTTPTransport(http2=True)
        retry_transport = RetryTransport(transport=base_transport, retry=retry)

        self.http_session = httpx.Client(
            transport=retry_transport,
            timeout=30.0,
            follow_redirects=True,
        )

        # Internal Fansly stuff
        self.check_key = check_key

        # Cache important data
        self.client_timestamp = self.get_client_timestamp()
        self.session_id = "null"

        # Device ID caching (rate-limiting/429)
        self.on_device_updated = on_device_updated

        if device_id is not None and device_id_timestamp is not None:
            self.device_id = device_id
            self.device_id_timestamp = device_id_timestamp

        else:
            self.device_id_timestamp = int(datetime(1990, 1, 1, 0, 0).timestamp())
            self.update_device_id()

        # Session setup is now async and must be done separately
        self.session_id = "null"

        # WebSocket client for persistent connection (anti-detection)
        self._websocket_client: FanslyWebSocket | None = None

    # region HTTP Header Management

    def get_text_accept(self) -> str:
        return "application/json, text/plain, */*"

    def set_text_accept(self, headers: dict[str, str]):
        headers["Accept"] = self.get_text_accept()

    def get_common_headers(self, alternate_token: str | None = None) -> dict[str, str]:
        token = self.token

        if alternate_token:
            token = alternate_token

        if token is None or self.user_agent is None:
            raise RuntimeError(
                "Internal error generating HTTP headers - token and user agent not set."
            )

        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "authorization": token,
            "Origin": "https://fansly.com",
            "Referer": "https://fansly.com/",
            "User-Agent": self.user_agent,
        }

        return headers

    def get_http_headers(
        self,
        url: str,
        add_fansly_headers: bool = True,
        alternate_token: str | None = None,
    ) -> dict[str, str]:
        headers = self.get_common_headers(alternate_token=alternate_token)

        self.set_text_accept(headers)

        if add_fansly_headers:
            # TODO: Add Fansly session auth headers
            fansly_headers = {
                # Device ID
                "fansly-client-id": self.device_id,
                # Mandatory: A client timestamp
                "fansly-client-ts": str(self.client_timestamp),
                # Kind of a security hash/signature
                "fansly-client-check": self.get_fansly_client_check(url),
            }

            # Mandatory: Session ID - from WebSockets, uaggghh
            # Not for /account/me
            # TODO
            if self.session_id != "null":
                fansly_headers["fansly-session-id"] = self.session_id

            headers = {**headers, **fansly_headers}

        return headers

    # endregion

    # region HTTP Query String Management

    def get_ngsw_params(self) -> dict[str, str]:
        return {
            "ngsw-bypass": "true",
        }

    # endregion

    # region HTTP Requests

    def cors_options_request(self, url: str) -> None:
        """Performs an OPTIONS CORS request to Fansly servers."""

        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Access-Control-Request-Headers": "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id",
            "Access-Control-Request-Method": "GET",
            "Origin": "https://fansly.com",
            "Referer": "https://fansly.com/",
            "User-Agent": self.user_agent,
        }

        self.http_session.options(
            url,
            headers=headers,
        )

    def get_with_ngsw(
        self,
        url: str,
        params: dict[str, str] = {},
        cookies: dict[str, str] = {},
        stream: bool = False,
        add_fansly_headers: bool = True,
        alternate_token: str | None = None,
        bypass_rate_limit: bool = False,
    ) -> httpx.Response:
        self.update_client_timestamp()

        # Rate limiting before request (synchronous)
        # Skip rate limiting for TS segments and other high-volume downloads
        if self.rate_limiter is not None and not bypass_rate_limit:
            self.rate_limiter.wait_for_request()

        default_params = self.get_ngsw_params()

        existing_params = get_flat_qs_dict(url)

        request_params = {
            **existing_params,
            **default_params,
            **params,
        }

        headers = self.get_http_headers(
            url=url,
            add_fansly_headers=add_fansly_headers,
            alternate_token=alternate_token,
        )

        self.cors_options_request(url)

        (_, file_url) = split_url(url)

        arguments = {
            "url": file_url,
            "params": request_params,
            "headers": headers,
        }

        if len(cookies) > 0:
            arguments["cookies"] = cookies

        # Make request and record response for rate limiter
        start_time = time.time()

        if stream:
            request = self.http_session.build_request("GET", **arguments)
            response = self.http_session.send(request, stream=True)
        else:
            response = self.http_session.get(**arguments)

        # Calculate response time
        response_time = time.time() - start_time

        # Extract endpoint name from URL for logging
        parsed_url = urlparse(file_url)
        endpoint = parsed_url.path.split("/")[-1] if parsed_url.path else "unknown"

        # Log request details
        bypassed_str = " [BYPASS]" if bypass_rate_limit else ""
        logger.debug(
            f"API Request: {endpoint} → HTTP {response.status_code} "
            f"({response_time:.2f}s){bypassed_str}"
        )

        # Record response for adaptive rate limiting (skip if bypassing)
        if self.rate_limiter is not None and not bypass_rate_limit:
            self.rate_limiter.record_response(response.status_code, response_time)

        return response

    def get_client_account_info(
        self, alternate_token: str | None = None
    ) -> httpx.Response:
        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/account/me",
            alternate_token=alternate_token,
        )

    def get_creator_account_info(self, creator_name: str | list[str]) -> httpx.Response:
        """Get account info by username(s).

        Args:
            creator_name: Single username or list of usernames

        Returns:
            Response containing account info
        """
        if isinstance(creator_name, list):
            creator_name = ",".join(creator_name)
        return self.get_with_ngsw(
            url=f"https://apiv3.fansly.com/api/v1/account?usernames={creator_name}",
        )

    def get_account_info_by_id(
        self, account_ids: str | int | list[str | int]
    ) -> httpx.Response:
        """Get account info by ID(s).

        Args:
            account_ids: Single account ID or list of IDs

        Returns:
            Response containing account info
        """
        if isinstance(account_ids, list):
            account_ids = ",".join(str(id) for id in account_ids)
        else:
            account_ids = str(account_ids)
        return self.get_with_ngsw(
            url=f"https://apiv3.fansly.com/api/v1/account?ids={account_ids}",
        )

    def get_media_collections(self) -> httpx.Response:
        custom_params = {
            "limit": "9999",
            "offset": "0",
        }

        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/account/media/orders/",
            params=custom_params,
        )

    def get_following_list(
        self,
        user_id: str | int,
        limit: int = 425,
        offset: int = 0,
        before: int = 0,
        after: int = 0,
    ) -> httpx.Response:
        """Get a page of accounts the user is following.

        Args:
            user_id: ID of the user to get following list for
            limit: Maximum number of results to return (default: 425)
            offset: Number of results to skip (default: 0)
            before: Get results before this timestamp (default: 0)
            after: Get results after this timestamp (default: 0)

        Returns:
            Response containing list of followed accounts
        """
        params = {
            "before": str(before),
            "after": str(after),
            "limit": str(limit),
            "offset": str(offset),
        }

        return self.get_with_ngsw(
            url=f"https://apiv3.fansly.com/api/v1/account/{user_id}/following",
            params=params,
        )

    def get_account_media(self, media_ids: str) -> httpx.Response:
        """Retrieve account media by ID(s).

        :param media_ids: Media ID(s) separated by comma w/o spaces.
        :type media_ids: str

        :return: A web request response
        :rtype: request.Response
        """
        return self.get_with_ngsw(
            f"https://apiv3.fansly.com/api/v1/account/media?ids={media_ids}",
        )

    def get_post(self, post_id: str) -> httpx.Response:
        custom_params = {
            "ids": post_id,
        }

        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/post",
            params=custom_params,
        )

    def get_timeline(self, creator_id: str, timeline_cursor: str) -> httpx.Response:
        custom_params = {
            "before": timeline_cursor,
            "after": "0",
            "wallId": "",
            "contentSearch": "",
        }

        return self.get_with_ngsw(
            url=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}",
            params=custom_params,
        )

    def get_wall_posts(
        self, creator_id: str, wall_id: str, before_cursor: str = "0"
    ) -> httpx.Response:
        """Get posts from a specific wall.

        Args:
            creator_id: The account ID of the creator
            wall_id: The ID of the wall to get posts from
            before_cursor: Post ID to get posts before (for pagination). Defaults to "0" for latest posts.

        Returns:
            Response containing wall posts. Each page returns up to 15 posts.
        """
        custom_params = {
            "before": before_cursor,
            "after": "0",
            "wallId": wall_id,
            "contentSearch": "",
        }

        return self.get_with_ngsw(
            url=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}",
            params=custom_params,
        )

    def get_group(self) -> httpx.Response:
        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/messaging/groups",
        )

    def get_message(self, params: dict[str, str]) -> httpx.Response:
        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/message",
            params=params,
        )

    def get_device_id_info(self) -> httpx.Response:
        return self.get_with_ngsw(
            url="https://apiv3.fansly.com/api/v1/device/id",
            add_fansly_headers=False,
        )

    # endregion

    # region WebSocket Communication

    async def get_active_session_async(self) -> str:
        """Get active session ID and start persistent WebSocket connection.

        This replaces the old one-time WebSocket connection with a persistent
        background connection for anti-detection purposes. The WebSocket client
        maintains the connection for the lifetime of the API instance.

        Returns:
            Session ID string

        Raises:
            RuntimeError: If WebSocket connection or authentication fails
        """
        # If WebSocket client already exists and is connected, reuse it
        if self._websocket_client is not None and self._websocket_client.connected:
            if self._websocket_client.session_id:
                logger.info(
                    "Reusing existing WebSocket session: %s",
                    self._websocket_client.session_id,
                )
                return self._websocket_client.session_id
            else:
                logger.warning("WebSocket connected but no session_id, reconnecting...")
                await self._websocket_client.stop()
                self._websocket_client = None

        # Create new WebSocket client with current session cookies
        logger.info("Starting persistent WebSocket connection for anti-detection")

        # Extract cookies as dict for WebSocket client
        cookies_dict = {}
        for cookie in self.http_session.cookies.jar:
            cookies_dict[cookie.name] = cookie.value

        self._websocket_client = FanslyWebSocket(
            token=self.token,
            user_agent=self.user_agent,
            cookies=cookies_dict,
            enable_logging=False,  # Set to True for debugging
            on_unauthorized=self._handle_websocket_unauthorized,
            on_rate_limited=self._handle_websocket_rate_limited,
        )

        try:
            # Start WebSocket in background (connects and authenticates)
            await self._websocket_client.start_background()

            # Wait a moment for authentication to complete
            import asyncio

            for _ in range(10):  # Wait up to 1 second
                if self._websocket_client.session_id:
                    break
                await asyncio.sleep(0.1)

            if not self._websocket_client.session_id:
                raise RuntimeError(
                    "WebSocket authentication failed - no session ID received"
                )

            logger.info(
                "WebSocket session established: %s", self._websocket_client.session_id
            )
            return self._websocket_client.session_id

        except Exception as e:
            logger.error("Failed to establish WebSocket session: %s", e)
            # Clean up on failure
            if self._websocket_client:
                await self._websocket_client.stop()
                self._websocket_client = None
            raise RuntimeError(f"WebSocket session setup failed: {e}")

    async def get_active_session(self) -> str:
        """Get active session ID asynchronously."""
        return await self.get_active_session_async()

    # region

    # region Utility Methods

    async def setup_session(self) -> bool:
        """Set up session asynchronously."""
        try:
            # Preflight auth - necessary for WebSocket request to succeed
            _ = self.get_json_response_contents(self.get_client_account_info())

            session_id = await self.get_active_session()

            self.session_id = session_id

        except Exception as ex:
            raise RuntimeError(f"Error during session setup: {ex}")

        return True

    def login(self, username: str, password: str) -> dict[str, Any]:
        """Login to Fansly and obtain session token.

        This performs the login flow to obtain an authorization token and session cookie.
        After successful login, the token is automatically set in the instance.

        NOTE: Firefox DevTools may show "No response data available" for the login
        response, but the authorization token and session ID are extracted from:
        1. The response JSON (if available)
        2. The f-s-c session cookie (base64 encoded)
        3. Subsequent API requests that use these values

        Args:
            username: Fansly username or email
            password: Account password

        Returns:
            dict containing login response data (may be empty if DevTools shows no data)

        Raises:
            RuntimeError: If login fails or response is invalid

        Example:
            # Create API instance with empty token initially
            api = FanslyApi(
                token="",  # Will be set by login
                user_agent=user_agent,
                check_key=check_key,
                device_id=device_id
            )
            response = api.login("username", "password")
            # Token and session_id are now set automatically from response/cookie

            # Verify login was successful
            if api.token and api.session_id != "null":
                print("Login successful!")
            else:
                print("Login may have failed - check logs")
        """
        login_url = "https://apiv3.fansly.com/api/v1/login?ngsw-bypass=true"

        # Update client timestamp before login
        self.update_client_timestamp()

        # Build request body (matches browser observation)
        body = {
            "username": username,
            "password": password,
            "deviceId": self.device_id,
        }

        # Build headers (matches browser observation, no authorization token for login)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Referer": "https://fansly.com/",
            "Origin": "https://fansly.com",
            "User-Agent": self.user_agent,
            "DNT": "1",
            "Sec-GPC": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            # Fansly-specific headers (observed in browser)
            "fansly-client-id": self.device_id,
            "fansly-client-ts": str(self.client_timestamp),
            "fansly-client-check": self.get_fansly_client_check(login_url),
        }

        logger.info(f"Attempting login for user: {username}")

        try:
            response = self.http_session.post(
                login_url,
                json=body,
                headers=headers,
            )

            # Validate response
            response.raise_for_status()

            # Parse response (may be empty if DevTools shows "No response data available")
            try:
                response_data = response.json()
                logger.debug(f"Login response data: {response_data}")
            except Exception as e:
                logger.warning(f"Could not parse login response JSON: {e}")
                response_data = {"success": True, "response": {}}

            # Extract session cookie (f-s-c) from response FIRST
            # This cookie is CRITICAL - it's used to derive the authorization token
            # Observed format: ODM4NTY5NDk3NTcxOTA5NjMyOjE6MTplMzIwZmU5ODUzYTU5YzU2YmU5ZGE2ODU5MWQ2Njk
            # Decoded: sessionId:1:1:hash
            session_cookie = None
            for cookie in response.cookies.jar:
                if cookie.name == "f-s-c":
                    session_cookie = cookie.value
                    if session_cookie and len(session_cookie) > 20:
                        logger.info(
                            f"Session cookie obtained: f-s-c={session_cookie[:20]}"
                        )
                    else:
                        logger.info("Session cookie obtained")
                    break

            if not session_cookie:
                raise RuntimeError("Login failed: No f-s-c session cookie in response")

            # Extract session ID from cookie
            # Cookie format (base64): sessionId:1:1:hash
            try:
                import base64

                # Add padding if needed
                padding = 4 - len(session_cookie) % 4
                if padding != 4:
                    session_cookie_padded = session_cookie + ("=" * padding)
                else:
                    session_cookie_padded = session_cookie

                decoded_cookie = base64.b64decode(session_cookie_padded).decode("utf-8")
                logger.debug(f"Decoded session cookie: {decoded_cookie}")

                # Format: sessionId:1:1:hash
                parts = decoded_cookie.split(":")
                if len(parts) >= 1 and parts[0].isdigit():
                    self.session_id = parts[0]
                    logger.info(f"Session ID extracted from cookie: {self.session_id}")
                else:
                    logger.warning(f"Unexpected cookie format: {decoded_cookie}")

            except Exception as e:
                logger.error(f"Could not extract session ID from cookie: {e}")
                raise RuntimeError(f"Login failed: Could not parse session cookie: {e}")

            # Extract authorization token from response
            # Based on observations:
            # - Authorization token format (base64): sessionId:1:2:hash
            # - Different from cookie which is: sessionId:1:1:hash
            # - The token is in response.response.session.token

            token_found = False
            if "response" in response_data and response_data["response"]:
                resp_inner = response_data["response"]
                logger.debug(
                    f"Response inner keys: {list(resp_inner.keys()) if resp_inner else 'empty'}"
                )

                # Check for token in session object (correct location)
                if "session" in resp_inner and isinstance(resp_inner["session"], dict):
                    session_data = resp_inner["session"]
                    if "token" in session_data and session_data["token"]:
                        self.token = session_data["token"]
                        logger.info(
                            "Authorization token obtained from response.session.token"
                        )
                        token_found = True

                # Fallback: try other possible locations
                if not token_found:
                    for key in [
                        "token",
                        "authorization",
                        "authToken",
                        "sessionToken",
                        "auth",
                    ]:
                        if key in resp_inner and resp_inner[key]:
                            self.token = resp_inner[key]
                            logger.info(
                                f"Authorization token obtained from response.{key}"
                            )
                            token_found = True
                            break

            if not token_found:
                logger.warning("Authorization token not found in response")
                logger.warning(
                    "This may be normal if DevTools shows 'No response data available'"
                )
                logger.warning(
                    "You may need to manually set the token from a subsequent API request"
                )
                logger.warning(
                    "Or the token might be provided through a different mechanism"
                )

            logger.info(f"Login successful for user: {username}")

            # Log status for debugging
            if self.token:
                logger.info("✓ Authorization token set")
            else:
                logger.warning(
                    "✗ Authorization token NOT set - may need manual configuration"
                )

            if self.session_id and self.session_id != "null":
                logger.info(f"✓ Session ID set: {self.session_id}")
            else:
                logger.warning("✗ Session ID NOT set")

            return response_data

        except httpx.HTTPStatusError as e:
            logger.error(f"Login failed with HTTP error: {e}")
            if e.response:
                logger.error(f"Response body: {e.response.text}")
            raise RuntimeError(
                f"Login failed: {e.response.status_code} - {e.response.text if e.response else 'Unknown'}"
            )
        except Exception as e:
            logger.error(f"Login failed with error: {e}")
            raise RuntimeError(f"Login failed: {e}")

    @staticmethod
    def get_timestamp_ms() -> int:
        timestamp = datetime.now(timezone.utc).timestamp()

        return int(timestamp * 1000)

    def get_client_timestamp(self) -> int:
        # this.currentCachedTimestamp_ =
        #   Date.now() + (5000 - Math.floor(10000 * Math.random()));
        # Date.now(): Return the number of milliseconds elapsed since midnight,
        #   January 1, 1970 Universal Coordinated Time (UTC).
        ms = self.get_timestamp_ms()

        random_value = 5000 - math.floor(10000 * random.random())

        fansly_client_ts = ms + random_value

        return fansly_client_ts

    def update_client_timestamp(self):
        new_timestamp = self.get_client_timestamp()

        if not hasattr(self, "client_timestamp"):
            return

        if new_timestamp > self.client_timestamp:
            self.client_timestamp = new_timestamp

    def to_str16(self, number: int) -> str:
        by = number.to_bytes(64, byteorder="big")

        raw_string = binascii.hexlify(by).decode("utf-8")

        return raw_string.lstrip("0")

    @staticmethod
    def int32(val):
        from ctypes import c_int32

        if -(2**31) <= val < 2**31:
            return val

        return c_int32(val).value

    @staticmethod
    def rshift32(number: int, bits: int):
        int_max_value = 0x100000000
        return number >> bits if number >= 0 else (number + int_max_value) >> bits

    @staticmethod
    def imul32(number1: int, number2: int) -> int:
        this = FanslyApi
        return this.int32(number1 * number2)

    @staticmethod
    def cyrb53(text: str, seed: int = 0) -> int:
        # https://github.com/mbaersch/cyrb53-hasher
        # https://stackoverflow.com/questions/7616461/generate-a-hash-from-string-in-javascript/52171480#52171480
        # cyrb53(message, seed = 0) {
        #     let h1 = 0xdeadbeef ^ seed, h2 = 0x41c6ce57 ^ seed;

        #     for (let charCode, strPos = 0; strPos < message.length; strPos++) {
        #         charCode = message.charCodeAt(strPos);
        #         h1 = this.imul(h1 ^ charCode, 2654435761);
        #         h2 = this.imul(h2 ^ charCode, 1597334677);
        #     }
        #     h1 = this.imul(h1 ^ h1 >>> 16, 2246822507);
        #     h1 ^= this.imul(h2 ^ h2 >>> 13, 3266489909);
        #     h2 = this.imul(h2 ^ h2 >>> 16, 2246822507);
        #     h2 ^= this.imul(h1 ^ h1 >>> 13, 3266489909);
        #     return 4294967296 * (2097151 & h2) + (h1 >>> 0);
        # }
        this = FanslyApi

        h1 = this.int32(0xDEADBEEF) ^ this.int32(seed)
        h2 = 0x41C6CE57 ^ this.int32(seed)

        for pos in range(len(text)):
            char_code = ord(text[pos])
            h1 = this.imul32(h1 ^ char_code, 2654435761)
            h2 = this.imul32(h2 ^ char_code, 1597334677)

        h1 = this.imul32(h1 ^ this.rshift32(h1, 16), 2246822507)
        h1 ^= this.imul32(h2 ^ this.rshift32(h2, 13), 3266489909)
        h2 = this.imul32(h2 ^ this.rshift32(h2, 16), 2246822507)
        h2 ^= this.imul32(h1 ^ this.rshift32(h1, 13), 3266489909)

        return 4294967296 * (2097151 & h2) + (this.rshift32(h1, 0))

    def get_fansly_client_check(self, url: str) -> str:
        # let urlPathName = new URL(url).pathname;
        # let uniqueClientUrlIdentifier = this.checkKey_ + '_' + urlPathName + '_' + deviceId;
        # let urlPathNameDigest = this.getDigestFromCache(urlPathName);
        #
        # urlPathNameDigest ||
        #   (urlPathNameDigest =
        #       this.cyrb53(uniqueClientUrlIdentifier).toString(16),
        #       this.cacheDigest(urlPathName,
        #       urlPathNameDigest));
        #
        # headers.push({
        #     key: 'fansly-client-check',
        #     value: urlPathNameDigest
        # });
        #
        # URL.pathname: https://developer.mozilla.org/en-US/docs/Web/API/URL/pathname
        url_path = urlparse(url).path

        unique_identifier = f"{self.check_key}_{url_path}_{self.device_id}"

        digest = self.cyrb53(unique_identifier)
        digest_str = self.to_str16(digest)

        return digest_str

    def validate_json_response(self, response: httpx.Response) -> bool:
        response.raise_for_status()

        if response.status_code != 200:
            raise RuntimeError(
                f"Fansly API: Web request failed: {response.status_code} - {response.reason_phrase}"
            )

        decoded_response = response.json()

        if "success" in decoded_response:
            if str(decoded_response["success"]).lower() == "true":
                return True

        raise RuntimeError(
            f"Fansly API: Invalid or failed JSON response:\n{decoded_response}"
        )

    @staticmethod
    def convert_ids_to_int(data: Any) -> Any:
        """Recursively convert ID fields from strings to integers in JSON responses.

        This is required for PostgreSQL compatibility, which strictly enforces type
        matching and won't auto-convert strings to BigInteger like SQLite does.

        Args:
            data: JSON data (dict, list, or primitive)

        Returns:
            Data with ID fields converted to integers
        """
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                # Convert fields ending with 'Id' or named 'id' from strings to ints
                if (key == "id" or key.endswith("Id")) and isinstance(value, str):
                    try:
                        result[key] = int(value)
                    except (ValueError, TypeError):
                        result[key] = value
                elif isinstance(value, (dict, list)):
                    result[key] = FanslyApi.convert_ids_to_int(value)
                else:
                    result[key] = value
            return result
        elif isinstance(data, list):
            return [FanslyApi.convert_ids_to_int(item) for item in data]
        else:
            return data

    def get_json_response_contents(self, response: httpx.Response) -> dict:
        self.validate_json_response(response)

        json_data = response.json()["response"]
        # Convert all ID fields from strings to integers for PostgreSQL compatibility
        return self.convert_ids_to_int(json_data)

    def get_client_user_name(self, alternate_token: str | None = None) -> str | None:
        """Fetches user account information for a particular authorization token.

        :param alternate_token: An alternate authorization token string for
            browser config probing. Defaults to `None` so the internal
            `token` provided during initialization will be used.

        :type alternate_token: Optional[str]

        :return: The account user name or None.
        :rtype: str | None
        """
        account_response = self.get_client_account_info(alternate_token=alternate_token)

        response_contents = self.get_json_response_contents(account_response)

        account_info = response_contents["account"]
        username = account_info["username"]

        if username:
            return username

        return None

    def get_device_id(self) -> str:
        device_response = self.get_device_id_info()

        return str(self.get_json_response_contents(device_response))

    def update_device_id(self) -> str:
        offset_minutes = 180

        offset_ms = offset_minutes * 60 * 1000

        current_ts = self.get_timestamp_ms()

        if current_ts > self.device_id_timestamp + offset_ms:
            self.device_id = self.get_device_id()
            self.device_id_timestamp = current_ts

            if self.on_device_updated is not None:
                self.on_device_updated()

        return self.device_id

    def _handle_websocket_unauthorized(self) -> None:
        """Handle 401 Unauthorized from WebSocket.

        This is called when the WebSocket receives a 401 error code,
        indicating the session is no longer valid. Clears the session
        and token to force re-authentication.
        """
        logger.warning("WebSocket reported 401 Unauthorized - session invalidated")
        self.session_id = "null"
        # Note: We don't clear self.token here because username/password login
        # flow may need it. The application should handle re-login if needed.

    def _handle_websocket_rate_limited(self) -> None:
        """Handle 429 Rate Limited from WebSocket (out-of-band).

        This is called when the WebSocket receives a 429 error code,
        triggering the rate limiter's adaptive backoff without an HTTP response.
        """
        logger.warning("WebSocket reported 429 Rate Limited - triggering rate limiter")
        if self.rate_limiter:
            # Trigger adaptive backoff by recording a 429 response
            # Use 0.0 for response_time since this is out-of-band
            self.rate_limiter.record_response(429, 0.0)
        else:
            logger.warning(
                "Rate limiter not available - cannot apply adaptive backoff from WebSocket 429"
            )

    async def close_websocket(self) -> None:
        """Close the persistent WebSocket connection.

        This method should be called when the API instance is no longer needed
        to properly clean up the background WebSocket connection.
        """
        if self._websocket_client is not None:
            logger.info("Closing persistent WebSocket connection")
            try:
                await self._websocket_client.stop()
            except Exception as e:
                logger.warning("Error stopping WebSocket: %s", e)
            finally:
                self._websocket_client = None

    def __del__(self) -> None:
        """Cleanup on instance destruction.

        Note: This is a synchronous destructor, so we can't properly await
        the async websocket cleanup. Users should call close_websocket() explicitly
        for proper cleanup. This is just a best-effort cleanup.
        """
        if self._websocket_client is not None:
            logger.warning(
                "FanslyApi instance destroyed with active WebSocket - "
                "call close_websocket() explicitly for proper cleanup"
            )

    # region
