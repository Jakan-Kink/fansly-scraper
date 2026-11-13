"""``options.proxy`` routes every Fansly connection; proxy environment variables route none.

Real sockets on 127.0.0.1 throughout: a recording SOCKS5 proxy in front of a
plain HTTP server and the scripted WebSocket server. respx is not used because
it replaces the transport and cannot show which route a request took.
"""

import asyncio

import pytest

from api.websocket import FanslyWebSocket, _ChildWebSocket
from download.livestream import _poll_segments_loop, _resolve_variant_url
from tests.fixtures.api import (
    DEFAULT_FAKE_TS_BYTES,
    dump_ws_server_state,
    fake_websocket_session,
)


class TestConfiguredProxy:
    """Each connection type reaches its target only through the proxy."""

    @pytest.mark.asyncio
    async def test_api_clients(self, mock_config, socks5_proxy, local_http_target):
        """Async and segment clients resolve a name only the proxy can reach."""
        local_http_target.routes["/media.bin"] = ("application/octet-stream", b"data")
        mock_config.proxy = socks5_proxy.url()
        api = mock_config.get_api()
        url = local_http_target.url("/media.bin", host="cdn.invalid")

        try:
            async_response = await api.get_with_ngsw(url, add_fansly_headers=False)
            sync_response = api.get_with_ngsw_sync(url, add_fansly_headers=False)
        finally:
            await api.aclose()

        assert async_response.content == b"data"
        assert sync_response.content == b"data"
        assert socks5_proxy.targets == [("cdn.invalid", local_http_target.port)] * 2
        assert local_http_target.requests == [("GET", "/media.bin")] * 2

    @pytest.mark.asyncio
    async def test_api_websocket_gets_the_proxy(self, mock_config):
        """The API hands its proxy to the WebSocket client it starts."""
        mock_config.proxy = "socks5h://127.0.0.1:1080"
        api = mock_config.get_api()

        with fake_websocket_session():
            try:
                await api.get_active_session()
                websocket_proxy = api._websocket_client.proxy
            finally:
                await api.aclose()

        assert websocket_proxy == "socks5h://127.0.0.1:1080"

    @pytest.mark.asyncio
    async def test_websocket(self, ws_server, socks5_proxy):
        """The in-process WebSocket connects through the proxy."""
        ws_server.set_session(id="123", websocket_session_id="456", account_id="789")
        ws = _ChildWebSocket(
            token="test-token",
            user_agent="test-ua",
            base_url=ws_server.base_url,
            proxy=socks5_proxy.url(),
        )

        try:
            await ws.connect()
            session_id = ws.session_id
        finally:
            await ws.disconnect()
            dump_ws_server_state(ws_server)

        assert session_id == "123"
        assert socks5_proxy.targets == [("127.0.0.1", ws_server.port)]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_websocket_subprocess(self, ws_server, socks5_proxy):
        """The proxy setting crosses into the WebSocket subprocess."""
        ws_server.set_session(id="123", websocket_session_id="456", account_id="789")
        ws = FanslyWebSocket(
            token="test-token",
            user_agent="test-ua",
            base_url=ws_server.base_url,
            proxy=socks5_proxy.url(),
        )

        ws.start_in_thread()
        try:
            for _ in range(100):
                if ws.session_id:
                    break
                await asyncio.sleep(0.1)
            session_id = ws.session_id
        finally:
            await ws.stop_thread()
            dump_ws_server_state(ws_server)

        assert session_id == "123"
        assert socks5_proxy.targets == [("127.0.0.1", ws_server.port)]

    @pytest.mark.asyncio
    async def test_livestream(self, socks5_proxy, local_livestream_target, tmp_path):
        """Master playlist, variant playlist and segment all use the proxy."""
        target = local_livestream_target
        proxy = socks5_proxy.url()

        variant_url = await _resolve_variant_url(
            target.url("/master.m3u8", host="ivs.invalid"), proxy
        )
        assert variant_url is not None
        assert variant_url == target.url("/variant.m3u8", host="ivs.invalid")
        segments, durations = await _poll_segments_loop(
            variant_url, tmp_path, asyncio.Event(), "[test]", proxy=proxy
        )

        assert [path.read_bytes() for path in segments] == [DEFAULT_FAKE_TS_BYTES]
        assert len(durations) == 1
        assert set(socks5_proxy.targets) == {("ivs.invalid", target.port)}
        assert target.requests == [
            ("GET", "/master.m3u8"),
            ("GET", "/variant.m3u8"),
            ("GET", "/seg0.ts"),
        ]


@pytest.mark.usefixtures("dead_proxy_env")
class TestEnvironmentIgnored:
    """With no proxy configured, connections are direct whatever the environment says."""

    @pytest.mark.asyncio
    async def test_api_clients(self, mock_config, local_http_target):
        local_http_target.routes["/media.bin"] = ("application/octet-stream", b"data")
        api = mock_config.get_api()
        url = local_http_target.url("/media.bin")

        try:
            async_response = await api.get_with_ngsw(url, add_fansly_headers=False)
            sync_response = api.get_with_ngsw_sync(url, add_fansly_headers=False)
        finally:
            await api.aclose()

        assert async_response.content == b"data"
        assert sync_response.content == b"data"

    @pytest.mark.asyncio
    async def test_websocket(self, ws_server):
        ws_server.set_session(id="123", websocket_session_id="456", account_id="789")
        ws = _ChildWebSocket(
            token="test-token", user_agent="test-ua", base_url=ws_server.base_url
        )

        try:
            await ws.connect()
            session_id = ws.session_id
        finally:
            await ws.disconnect()
            dump_ws_server_state(ws_server)

        assert session_id == "123"

    @pytest.mark.asyncio
    async def test_livestream(self, local_livestream_target, tmp_path):
        target = local_livestream_target

        variant_url = await _resolve_variant_url(target.url("/master.m3u8"))
        assert variant_url is not None
        assert variant_url == target.url("/variant.m3u8")
        segments, _ = await _poll_segments_loop(
            variant_url, tmp_path, asyncio.Event(), "[test]"
        )

        assert [path.read_bytes() for path in segments] == [DEFAULT_FAKE_TS_BYTES]
