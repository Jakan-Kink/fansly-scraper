"""Fansly Web API"""

import asyncio
import base64
import binascii
import math
import time
from collections.abc import Callable
from ctypes import c_int32
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

import httpx
from httpx_retries import Retry, RetryTransport

from api.websocket import FanslyWebSocket
from config.logging import textio_logger as logger
from helpers.timer import timing_jitter
from helpers.web import get_flat_qs_dict, split_url


if TYPE_CHECKING:
    from api.rate_limiter import RateLimiter
    from config import FanslyConfig


class FanslyApi:
    # ── URL constants ────────────────────────────────────────────────
    # Source-of-truth for every Fansly host/path in the codebase. Tests
    # import these and respx routes match against them, so any future host
    # change (staging, version bump) is a single edit here. Endpoints with
    # `{}` placeholders use ``.format(...)`` at call sites.
    BASE_URL: ClassVar[str] = "https://apiv3.fansly.com/api/v1/"
    FANSLY_HOST: ClassVar[str] = "https://fansly.com"
    WS_URL: ClassVar[str] = "wss://wsv3.fansly.com"

    # ── Endpoint templates (properties so BASE_URL can be overridden) ──
    @property
    def ACCOUNT_ME_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account/me"

    @property
    def ACCOUNT_BY_USERNAME_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account?usernames={{}}"

    @property
    def ACCOUNT_BY_ID_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account?ids={{}}"

    @property
    def ACCOUNT_MEDIA_ORDERS_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account/media/orders/"

    @property
    def FOLLOWING_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account/{{}}/following"

    @property
    def ACCOUNT_MEDIA_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}account/media?ids={{}}"

    @property
    def POST_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}post"

    @property
    def TIMELINE_NEW_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}timelinenew/{{}}"

    @property
    def TIMELINE_HOME_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}timeline/home"

    @property
    def MEDIA_STORIES_NEW_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}mediastoriesnew"

    @property
    def MEDIA_STORIES_FOLLOWING_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}mediastories/following"

    @property
    def STREAMING_FOLLOWING_ONLINE_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}streaming/followingstreams/online"

    @property
    def STREAMING_CHANNEL_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}streaming/channel/{{}}"

    @property
    def CHATROOM_MESSAGES_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}chatroom/messages"

    @property
    def CHATROOM_SUBALERT_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}chatroom/subalert"

    @property
    def CHATROOMS_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}chatrooms"

    @property
    def CHATROOMS_SETTINGS_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}chatrooms/settings"

    @property
    def MEDIA_STORY_VIEW_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}mediastory/view"

    @property
    def MESSAGING_GROUPS_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}messaging/groups"

    @property
    def MESSAGE_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}message"

    @property
    def DEVICE_ID_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}device/id"

    @property
    def LOGIN_ENDPOINT(self) -> str:
        return f"{self.BASE_URL}login?ngsw-bypass=true"

    def __init__(
        self,
        token: str,
        user_agent: str,
        check_key: str,
        device_id: str | None = None,
        device_id_timestamp: int | None = None,
        on_device_updated: Callable[[], Any] | None = None,
        rate_limiter: "RateLimiter | None" = None,
        config: "FanslyConfig | None" = None,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter
        self.config = config

        # 429 excluded from forcelist when rate_limiter is active — get_with_ngsw
        # retries via RateLimiter instead so adaptive backoff sees the violation.
        retry = Retry(
            total=3,
            backoff_factor=0.5,  # 0.5s base delay with exponential backoff: 0.5s, 1s, 2s
            status_forcelist=(
                [418, 429, 500, 502, 503, 504]
                if rate_limiter is None
                else [418, 500, 502, 503, 504]
            ),
        )

        # Shared cookie jar across async runtime client, sync segment client, and WS.
        self._cookies = httpx.Cookies()

        async_base_transport = httpx.AsyncHTTPTransport(http2=True)
        async_retry_transport = RetryTransport(
            transport=async_base_transport, retry=retry
        )
        self.http_session = httpx.AsyncClient(
            transport=async_retry_transport,
            timeout=30.0,
            follow_redirects=True,
            cookies=self._cookies,
        )

        # Sync client for thread-pool callers — httpx.AsyncClient is not thread-safe across loops.
        self._segment_retry = retry
        self._segment_session: httpx.Client | None = None

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
            self.device_id = None
            self.device_id_timestamp = int(
                datetime(1990, 1, 1, 0, 0, tzinfo=UTC).timestamp()
            )

        self._websocket_client: FanslyWebSocket | None = None

    # region HTTP Header Management

    def get_text_accept(self) -> str:
        return "application/json, text/plain, */*"

    def set_text_accept(self, headers: dict[str, str]) -> None:
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
            "Origin": self.FANSLY_HOST,
            "Referer": f"{self.FANSLY_HOST}/",
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
            fansly_headers = {
                "fansly-client-id": self.device_id,
                # Mandatory: A client timestamp
                "fansly-client-ts": str(self.client_timestamp),
                # Kind of a security hash/signature
                "fansly-client-check": self.get_fansly_client_check(url),
            }

            # Mandatory: Session ID from WebSockets. Not for /account/me.
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

    def _get_segment_session(self) -> httpx.Client:
        """Lazy sync httpx client for m3u8 segment ThreadPoolExecutor."""
        if self._segment_session is None:
            sync_base_transport = httpx.HTTPTransport(http2=True)
            sync_retry_transport = RetryTransport(
                transport=sync_base_transport, retry=self._segment_retry
            )
            self._segment_session = httpx.Client(
                transport=sync_retry_transport,
                timeout=30.0,
                follow_redirects=True,
                cookies=self._cookies,
            )
        return self._segment_session

    async def cors_options_request(self, url: str) -> None:
        """Performs an OPTIONS CORS request to Fansly servers."""

        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Access-Control-Request-Headers": "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id",
            "Access-Control-Request-Method": "GET",
            "Origin": self.FANSLY_HOST,
            "Referer": f"{self.FANSLY_HOST}/",
            "User-Agent": self.user_agent,
        }

        await self.http_session.options(
            url,
            headers=headers,
        )

    def _summarize_request(
        self,
        file_url: str,
        request_params: dict[str, str],
        status_code: int,
        response_time: float,
        bypass_rate_limit: bool,
    ) -> None:
        """Log a one-line API request summary."""
        parsed_url = urlparse(file_url)
        parts = [p for p in parsed_url.path.split("/") if p and p not in ("api", "v1")]
        while parts and parts[-1].isdigit():
            parts.pop()
        endpoint = "/".join(parts) if parts else "unknown"

        _skip = {"ngsw-bypass", "fansly-client-id", "fansly-client-ts"}
        param_parts = []
        for k, v in request_params.items():
            if k in _skip:
                continue
            sv = str(v)
            if "," in sv and len(sv) > 40:
                param_parts.append(f"{k}=<{sv.count(',') + 1} ids>")
            elif len(sv) > 50:
                param_parts.append(f"{k}={sv[:20]}…")
            else:
                param_parts.append(f"{k}={sv}")
        param_str = f" [{', '.join(param_parts)}]" if param_parts else ""
        bypassed_str = " [BYPASS]" if bypass_rate_limit else ""
        logger.debug(
            f"API Request: {endpoint} → HTTP {status_code} "
            f"({response_time:.2f}s){bypassed_str}{param_str}"
        )

    async def get_with_ngsw(
        self,
        url: str,
        params: dict[str, str] = {},  # noqa: B006 - not mutated, only unpacked
        cookies: dict[str, str] = {},  # noqa: B006 - not mutated, only read
        stream: bool = False,
        add_fansly_headers: bool = True,
        alternate_token: str | None = None,
        bypass_rate_limit: bool = False,
    ) -> httpx.Response:
        # Skipping when add_fansly_headers=False breaks recursion: get_device_id_info
        # itself enters here with add_fansly_headers=False.
        if add_fansly_headers and self.device_id is None:
            await self.update_device_id()

        self.update_client_timestamp()

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

        await self.cors_options_request(url)

        (_, file_url) = split_url(url)

        arguments = {
            "url": file_url,
            "params": request_params,
            "headers": headers,
        }

        if len(cookies) > 0:
            arguments["cookies"] = cookies

        max_retries = self.config.api_max_retries if self.config else 1
        has_rate_limiter = self.rate_limiter is not None and not bypass_rate_limit

        for attempt in range(max_retries):
            if has_rate_limiter:
                await self.rate_limiter.async_wait_for_request()

            start_time = time.time()

            if stream:
                request = self.http_session.build_request("GET", **arguments)
                response = await self.http_session.send(request, stream=True)
            else:
                response = await self.http_session.get(**arguments)

            response_time = time.time() - start_time

            self._summarize_request(
                file_url,
                request_params,
                response.status_code,
                response_time,
                bypass_rate_limit,
            )

            if has_rate_limiter:
                self.rate_limiter.record_response(response.status_code, response_time)

            if (
                response.status_code == 429
                and has_rate_limiter
                and attempt < max_retries - 1
            ):
                logger.debug(
                    f"Rate limited (429), retrying ({attempt + 1}/{max_retries})..."
                )
                continue

            break

        return response

    def get_with_ngsw_sync(
        self,
        url: str,
        params: dict[str, str] = {},  # noqa: B006 - not mutated, only unpacked
        cookies: dict[str, str] = {},  # noqa: B006 - not mutated, only read
        stream: bool = False,
        add_fansly_headers: bool = True,
        alternate_token: str | None = None,
        bypass_rate_limit: bool = False,
    ) -> httpx.Response:
        """Sync variant for the m3u8 segment ThreadPoolExecutor."""
        self.update_client_timestamp()

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

        # CORS preflight (sync variant).
        sync = self._get_segment_session()
        cors_headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Access-Control-Request-Headers": "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id",
            "Access-Control-Request-Method": "GET",
            "Origin": self.FANSLY_HOST,
            "Referer": f"{self.FANSLY_HOST}/",
            "User-Agent": self.user_agent,
        }
        sync.options(url, headers=cors_headers)

        (_, file_url) = split_url(url)

        arguments = {
            "url": file_url,
            "params": request_params,
            "headers": headers,
        }
        if len(cookies) > 0:
            arguments["cookies"] = cookies

        max_retries = self.config.api_max_retries if self.config else 1
        has_rate_limiter = self.rate_limiter is not None and not bypass_rate_limit

        for attempt in range(max_retries):
            if has_rate_limiter:
                self.rate_limiter.wait_for_request()

            start_time = time.time()

            if stream:
                request = sync.build_request("GET", **arguments)
                response = sync.send(request, stream=True)
            else:
                response = sync.get(**arguments)

            response_time = time.time() - start_time

            self._summarize_request(
                file_url,
                request_params,
                response.status_code,
                response_time,
                bypass_rate_limit,
            )

            if has_rate_limiter:
                self.rate_limiter.record_response(response.status_code, response_time)

            if (
                response.status_code == 429
                and has_rate_limiter
                and attempt < max_retries - 1
            ):
                logger.debug(
                    f"Rate limited (429), retrying ({attempt + 1}/{max_retries})..."
                )
                continue

            break

        return response

    async def get_client_account_info(
        self, alternate_token: str | None = None
    ) -> httpx.Response:
        return await self.get_with_ngsw(
            url=self.ACCOUNT_ME_ENDPOINT,
            alternate_token=alternate_token,
        )

    async def get_creator_account_info(
        self, creator_name: str | list[str]
    ) -> httpx.Response:
        """Get account info by username(s).

        Args:
            creator_name: Single username or list of usernames

        Returns:
            Response containing account info
        """
        if isinstance(creator_name, list):
            creator_name = ",".join(creator_name)
        return await self.get_with_ngsw(
            url=self.ACCOUNT_BY_USERNAME_ENDPOINT.format(creator_name),
        )

    async def get_account_info_by_id(
        self, account_ids: str | int | list[str | int]
    ) -> httpx.Response:
        """Get account info by ID(s).

        Args:
            account_ids: Single account ID or list of IDs

        Returns:
            Response containing account info
        """
        if isinstance(account_ids, list):
            account_ids = ",".join(str(account_id) for account_id in account_ids)
        else:
            account_ids = str(account_ids)
        return await self.get_with_ngsw(
            url=self.ACCOUNT_BY_ID_ENDPOINT.format(account_ids),
        )

    async def get_media_collections(self) -> httpx.Response:
        custom_params = {
            "limit": "9999",
            "offset": "0",
        }

        return await self.get_with_ngsw(
            url=self.ACCOUNT_MEDIA_ORDERS_ENDPOINT,
            params=custom_params,
        )

    async def get_following_list(
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

        return await self.get_with_ngsw(
            url=self.FOLLOWING_ENDPOINT.format(user_id),
            params=params,
        )

    async def get_account_media(self, media_ids: str) -> httpx.Response:
        """Retrieve account media by ID(s).

        :param media_ids: Media ID(s) separated by comma w/o spaces.
        :type media_ids: str

        :return: A web request response
        :rtype: request.Response
        """
        return await self.get_with_ngsw(
            self.ACCOUNT_MEDIA_ENDPOINT.format(media_ids),
        )

    async def get_post(self, post_id: str) -> httpx.Response:
        custom_params = {
            "ids": post_id,
        }

        return await self.get_with_ngsw(
            url=self.POST_ENDPOINT,
            params=custom_params,
        )

    async def get_timeline(
        self, creator_id: int | str, timeline_cursor: str
    ) -> httpx.Response:
        custom_params = {
            "before": timeline_cursor,
            "after": "0",
            "wallId": "",
            "contentSearch": "",
        }

        return await self.get_with_ngsw(
            url=self.TIMELINE_NEW_ENDPOINT.format(creator_id),
            params=custom_params,
        )

    async def get_wall_posts(
        self, creator_id: int | str, wall_id: int | str, before_cursor: str = "0"
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

        return await self.get_with_ngsw(
            url=self.TIMELINE_NEW_ENDPOINT.format(creator_id),
            params=custom_params,
        )

    async def get_home_timeline(self) -> httpx.Response:
        """Fetch home timeline for all followed creators.

        Returns:
            Response containing home timeline posts for all followed creators.
            Used by the daemon monitor to detect which followed creators posted
            new content with a single fleet-wide call.
        """
        return await self.get_with_ngsw(
            url=self.TIMELINE_HOME_ENDPOINT,
            params={"before": "0", "after": "0", "mode": "0"},
        )

    async def get_media_stories(self, account_id: int | str) -> httpx.Response:
        """Fetch active media stories for a creator account.

        Args:
            account_id: The account ID of the creator

        Returns:
            Response containing mediaStories list and aggregationData.accountMedia
        """
        return await self.get_with_ngsw(
            url=self.MEDIA_STORIES_NEW_ENDPOINT,
            params={"accountId": str(account_id)},
        )

    async def get_story_states_following(self) -> httpx.Response:
        """Fetch story states for all followed creators.

        Returns:
            Response containing story state entries for followed creators,
            each with accountId and hasActiveStories. Used by the daemon
            monitor to detect when hasActiveStories flips true.
        """
        return await self.get_with_ngsw(
            url=self.MEDIA_STORIES_FOLLOWING_ENDPOINT,
            params={"limit": "100", "offset": "0"},
        )

    async def get_following_streams_online(self) -> httpx.Response:
        """Fetch the list of followed creators who are currently live.

        Returns:
            Response with ``streams`` list and ``aggregationData.accounts``.
            Each stream entry includes ``id``, ``channelId``, ``accountId``,
            ``title``, ``status``, ``viewerCount``, ``startedAt``.
            The accounts list includes full account data with
            ``streaming.channel.playbackUrl``.
        """
        return await self.get_with_ngsw(
            url=self.STREAMING_FOLLOWING_ONLINE_ENDPOINT,
        )

    async def get_streaming_channel(self, account_id: int | str) -> httpx.Response:
        """Fetch a creator's streaming channel, including authenticated playback URL.

        The returned ``stream.playbackUrl`` carries a short-lived JWT ``?token=``
        query param required for IVS playback. Call this immediately before
        starting a recording — the token expires within ~60 seconds.

        Args:
            account_id: The creator's account ID.

        Returns:
            Response with channel data including ``stream.playbackUrl`` (authenticated).
        """
        return await self.get_with_ngsw(
            url=self.STREAMING_CHANNEL_ENDPOINT.format(account_id),
        )

    async def get_chatroom_messages(self, chat_room_id: int | str) -> httpx.Response:
        """Fetch recent messages for a live chatroom.

        Args:
            chat_room_id: The chatroom ID (from ``channel.chatRoomId``).

        Returns:
            Response with ``messages`` list and ``aggregationData.accounts``.
            Each message includes ``id``, ``senderId``, ``type``, ``content``,
            ``createdAt``, ``username``, ``displayname``.
        """
        return await self.get_with_ngsw(
            url=self.CHATROOM_MESSAGES_ENDPOINT,
            params={"chatRoomId": str(chat_room_id)},
        )

    async def get_chatrooms(self, ids: list[int | str] | int | str) -> httpx.Response:
        """Fetch chatroom metadata for one or more chatroom IDs.

        Args:
            ids: Single chatroom ID or list of chatroom IDs.

        Returns:
            Response with list of chatroom objects including ``permissions``,
            ``activeGoals``, and ``access``.
        """
        if isinstance(ids, (int, str)):
            ids = [ids]
        return await self.get_with_ngsw(
            url=self.CHATROOMS_ENDPOINT,
            params={"ids": ",".join(str(i) for i in ids)},
        )

    async def get_chatrooms_settings(self) -> httpx.Response:
        """Fetch the authenticated user's chatroom settings.

        Returns:
            Response with chatroom settings (may be null if not configured).
        """
        return await self.get_with_ngsw(
            url=self.CHATROOMS_SETTINGS_ENDPOINT,
        )

    async def post_chatroom_subalert(self, payload: dict) -> httpx.Response:
        """Acknowledge a subscription alert in a live chatroom (POST).

        Called when the WebSocket dispatches a ``subalert`` event so the
        server records that the client received the notification.

        Args:
            payload: JSON body forwarded verbatim to the endpoint.
                Expected keys: ``chatRoomId``, ``senderId``, ``historyId``,
                ``subscriberId``, ``subscriptionTierId``.

        Returns:
            Response confirming the alert was recorded.
        """
        url = self.CHATROOM_SUBALERT_ENDPOINT
        self.update_client_timestamp()
        await self.cors_options_request(url)
        headers = self.get_http_headers(url=url, add_fansly_headers=True)

        if self.rate_limiter is not None:
            await self.rate_limiter.async_wait_for_request()

        start_time = time.time()
        response = await self.http_session.post(
            url,
            json=payload,
            headers=headers,
            params=self.get_ngsw_params(),
        )
        if self.rate_limiter is not None:
            self.rate_limiter.record_response(
                response.status_code, time.time() - start_time
            )
        return response

    async def mark_story_viewed(self, story_id: int | str) -> httpx.Response:
        """Mark a story as viewed (POST /api/v1/mediastory/view).

        Args:
            story_id: The story ID to mark as viewed

        Returns:
            Response with storyId and accountId confirmation
        """
        url = self.MEDIA_STORY_VIEW_ENDPOINT
        self.update_client_timestamp()
        await self.cors_options_request(url)
        headers = self.get_http_headers(url=url, add_fansly_headers=True)

        if self.rate_limiter is not None:
            await self.rate_limiter.async_wait_for_request()

        start_time = time.time()
        response = await self.http_session.post(
            url,
            json={"storyId": str(story_id)},
            headers=headers,
            params=self.get_ngsw_params(),
        )
        if self.rate_limiter is not None:
            self.rate_limiter.record_response(
                response.status_code, time.time() - start_time
            )
        return response

    async def get_group(self) -> httpx.Response:
        return await self.get_with_ngsw(
            url=self.MESSAGING_GROUPS_ENDPOINT,
        )

    async def get_message(self, params: dict[str, str]) -> httpx.Response:
        return await self.get_with_ngsw(
            url=self.MESSAGE_ENDPOINT,
            params=params,
        )

    async def get_device_id_info(self) -> httpx.Response:
        return await self.get_with_ngsw(
            url=self.DEVICE_ID_ENDPOINT,
            add_fansly_headers=False,
        )

    # endregion

    # region WebSocket Communication

    async def get_active_session(self) -> str:
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
                    "Reusing existing WebSocket session: {}",
                    self._websocket_client.session_id,
                )
                return self._websocket_client.session_id
            logger.warning("WebSocket connected but no session_id, reconnecting...")
            await self._websocket_client.stop_thread()
            self._websocket_client = None

        # http_client= shares the cookie jar with HTTP for bidirectional rotation sync.
        logger.info("Starting persistent WebSocket connection for anti-detection")

        self._websocket_client = FanslyWebSocket(
            token=self.token,
            user_agent=self.user_agent,
            http_client=self.http_session,
            enable_logging=False,
            on_unauthorized=self._handle_websocket_unauthorized,
            on_rate_limited=self._handle_websocket_rate_limited,
        )

        try:
            self._websocket_client.start_in_thread()

            # 5s budget covers both in-thread (~700ms) and subprocess (~1.2s) paths.
            for _ in range(50):
                if self._websocket_client.session_id:
                    break
                await asyncio.sleep(0.1)

            if not self._websocket_client.session_id:
                raise RuntimeError(
                    "WebSocket authentication failed - no session ID received"
                )
        except Exception as e:
            logger.error("Failed to establish WebSocket session: {}", e)
            if self._websocket_client:
                await self._websocket_client.stop_thread()
                self._websocket_client = None
            raise RuntimeError(f"WebSocket session setup failed: {e}") from e
        else:
            logger.info(
                "WebSocket session established: {}", self._websocket_client.session_id
            )
            return self._websocket_client.session_id

    # region

    # region Utility Methods

    async def setup_session(self) -> bool:
        """Set up session asynchronously."""
        try:
            # Preflight auth - necessary for WebSocket request to succeed
            _ = self.get_json_response_contents(await self.get_client_account_info())

            session_id = await self.get_active_session()

            self.session_id = session_id

        except Exception as ex:
            raise RuntimeError(f"Error during session setup: {ex}")

        return True

    async def login(self, username: str, password: str) -> dict[str, Any]:
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
        login_url = self.LOGIN_ENDPOINT

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
            "Referer": f"{self.FANSLY_HOST}/",
            "Origin": self.FANSLY_HOST,
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
            response = await self.http_session.post(
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

            # f-s-c cookie is CRITICAL — used to derive the authorization token.
            # Observed format (base64): sessionId:1:1:hash
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

            # Explicitly mirror f-s-c into the shared jar so async runtime + sync
            # segment client + WS all carry it without relying on httpx's
            # response-cookie auto-merge behavior.
            self._cookies.set("f-s-c", session_cookie, domain="fansly.com", path="/")

            # Extract session ID from cookie
            # Cookie format (base64): sessionId:1:1:hash
            try:
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
                raise RuntimeError(
                    f"Login failed: Could not parse session cookie: {e}"
                ) from e

            # Authorization token format (base64) is sessionId:1:2:hash
            # (cookie is :1:1:hash). Located at response.response.session.token.
            token_found = False
            if response_data.get("response"):
                resp_inner = response_data["response"]
                logger.debug(
                    f"Response inner keys: {list(resp_inner.keys()) if resp_inner else 'empty'}"
                )

                # Check for token in session object (correct location)
                if "session" in resp_inner and isinstance(resp_inner["session"], dict):
                    session_data = resp_inner["session"]
                    if session_data.get("token"):
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
                        if resp_inner.get(key):
                            self.token = resp_inner[key]
                            logger.info(
                                f"Authorization token obtained from response.{key}"
                            )
                            token_found = True
                            break

            if not token_found:
                raise RuntimeError(
                    "Login response did not contain an authorization token"
                )

            logger.info(f"Login successful for user: {username}")
            logger.info(f"✓ Session ID set: {self.session_id}")

            return response_data  # noqa: TRY300 - already in success path, adding else would add unnecessary nesting

        except httpx.HTTPStatusError as e:
            logger.error(f"Login failed with HTTP error: {e}")
            if e.response:
                logger.error(f"Response body: {e.response.text}")
            raise RuntimeError(
                f"Login failed: {e.response.status_code} - {e.response.text if e.response else 'Unknown'}"
            ) from e
        except Exception as e:
            logger.error(f"Login failed with error: {e}")
            raise RuntimeError(f"Login failed: {e}") from e

    @staticmethod
    def get_timestamp_ms() -> int:
        timestamp = datetime.now(UTC).timestamp()

        return int(timestamp * 1000)

    def get_client_timestamp(self) -> int:
        # this.currentCachedTimestamp_ =
        #   Date.now() + (5000 - Math.floor(10000 * Math.random()));
        # Date.now(): Return the number of milliseconds elapsed since midnight,
        #   January 1, 1970 Universal Coordinated Time (UTC).
        ms = self.get_timestamp_ms()

        random_value = 5000 - math.floor(10000 * timing_jitter(0.0, 1.0))

        fansly_client_ts = ms + random_value

        return fansly_client_ts

    def update_client_timestamp(self) -> None:
        new_timestamp = self.get_client_timestamp()

        if not hasattr(self, "client_timestamp"):
            return

        self.client_timestamp = max(self.client_timestamp, new_timestamp)

    def to_str16(self, number: int) -> str:
        by = number.to_bytes(64, byteorder="big")

        raw_string = binascii.hexlify(by).decode("utf-8")

        return raw_string.lstrip("0")

    @staticmethod
    def int32(val: int) -> int:
        if -(2**31) <= val < 2**31:
            return val

        return c_int32(val).value

    @staticmethod
    def rshift32(number: int, bits: int) -> int:
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

        if (
            "success" in decoded_response
            and str(decoded_response["success"]).lower() == "true"
        ):
            return True

        raise RuntimeError(
            f"Fansly API: Invalid or failed JSON response:\n{decoded_response}"
        )

    @staticmethod
    def convert_ids_to_int(data: Any) -> Any:
        """Recursively convert ID fields from strings to integers.

        The Fansly API returns all IDs as strings. Converting at the API
        boundary avoids str/int mismatches in cache lookups, asyncpg params,
        and dict comparisons throughout the codebase.
        """
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if (key == "id" or key.endswith("Id")) and isinstance(value, str):
                    try:
                        result[key] = int(value)
                    except (ValueError, TypeError):
                        result[key] = value
                elif key.endswith("Ids") and isinstance(value, list):
                    result[key] = [
                        int(item) if isinstance(item, str) else item for item in value
                    ]
                elif isinstance(value, (dict, list)):
                    result[key] = FanslyApi.convert_ids_to_int(value)
                else:
                    result[key] = value
            return result
        if isinstance(data, list):
            return [FanslyApi.convert_ids_to_int(item) for item in data]
        return data

    def get_json_response_contents(self, response: httpx.Response) -> dict:
        """Validate response, extract payload, convert string IDs to ints."""
        self.validate_json_response(response)
        json_data = response.json()["response"]
        return self.convert_ids_to_int(json_data)

    async def get_client_user_name(
        self, alternate_token: str | None = None
    ) -> str | None:
        """Fetches user account information for a particular authorization token.

        :param alternate_token: An alternate authorization token string for
            browser config probing. Defaults to `None` so the internal
            `token` provided during initialization will be used.

        :type alternate_token: Optional[str]

        :return: The account user name or None.
        :rtype: str | None
        """
        account_response = await self.get_client_account_info(
            alternate_token=alternate_token
        )

        response_contents = self.get_json_response_contents(account_response)

        account_info = response_contents["account"]
        username = account_info["username"]

        if username:
            return username

        return None

    async def update_device_id(self) -> str:
        """Bootstrap or refresh device_id when missing or stale (>180 min)."""
        offset_minutes = 180
        offset_ms = offset_minutes * 60 * 1000
        current_ts = self.get_timestamp_ms()

        if self.device_id is None or current_ts > self.device_id_timestamp + offset_ms:
            response = await self.get_device_id_info()
            self.device_id = str(self.get_json_response_contents(response))
            self.device_id_timestamp = current_ts

            if self.on_device_updated is not None:
                self.on_device_updated()

        assert self.device_id is not None  # noqa: S101  # nosec B101  type narrowing for return
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

    async def aclose(self) -> None:
        """Close the persistent WebSocket connection.

        This method should be called when the API instance is no longer needed
        to properly clean up the background WebSocket connection.
        """
        if self._websocket_client is not None:
            logger.info("Closing persistent WebSocket connection")
            try:
                await self._websocket_client.stop_thread()
            except Exception as e:
                logger.warning("Error stopping WebSocket: {}", e)
            finally:
                self._websocket_client = None

        if self._segment_session is not None:
            try:
                self._segment_session.close()
            except Exception as e:
                logger.warning("Error closing segment session: {}", e)
            finally:
                self._segment_session = None

        try:
            await self.http_session.aclose()
        except Exception as e:
            logger.warning("Error closing http session: {}", e)

    async def close_websocket(self) -> None:
        """Backwards-compatible alias for aclose()."""
        await self.aclose()

    def __del__(self) -> None:
        """Best-effort warn on undisposed resources — call aclose() for clean teardown."""
        if self._websocket_client is not None:
            logger.warning(
                "FanslyApi instance destroyed with active WebSocket - "
                "call aclose() explicitly for proper cleanup"
            )

    # region
