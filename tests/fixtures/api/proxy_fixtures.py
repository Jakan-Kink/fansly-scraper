"""Loopback SOCKS5 proxy and HTTP target for proxy-routing tests.

respx replaces the transport, so it cannot show which route a request took.
These run real sockets on 127.0.0.1 and record what reached them.
"""

import select
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from .livestream_factories import build_master_playlist, build_variant_playlist
from .livestream_fixtures import DEFAULT_FAKE_TS_BYTES


_BIND_HOST = "127.0.0.1"
_SOCKS_REPLY_TAIL = b"\x00\x01" + bytes(6)
_PROXY_ENV_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("peer closed during the SOCKS handshake")
        data += chunk
    return data


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    while True:
        readable, _, _ = select.select([client, upstream], [], [], 30)
        if not readable:
            return
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


class _Socks5Handler(socketserver.BaseRequestHandler):
    server: "Socks5ProxyServer"

    def handle(self) -> None:
        client: socket.socket = self.request
        try:
            _version, method_count = _recv_exact(client, 2)
            _recv_exact(client, method_count)
            client.sendall(b"\x05\x00")
            address_type = _recv_exact(client, 4)[3]
            if address_type == 1:
                host = socket.inet_ntoa(_recv_exact(client, 4))
            elif address_type == 3:
                host = _recv_exact(client, _recv_exact(client, 1)[0]).decode()
            else:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exact(client, 16))
            port = int.from_bytes(_recv_exact(client, 2), "big")
            self.server.targets.append((host, port))
            if host != _BIND_HOST and not host.endswith(".invalid"):
                client.sendall(b"\x05\x02" + _SOCKS_REPLY_TAIL)
                return
            try:
                upstream = socket.create_connection((_BIND_HOST, port), timeout=5)
            except OSError:
                client.sendall(b"\x05\x05" + _SOCKS_REPLY_TAIL)
                return
            client.sendall(b"\x05\x00" + _SOCKS_REPLY_TAIL)
            with upstream:
                _relay(client, upstream)
        except OSError:
            return


class Socks5ProxyServer(socketserver.ThreadingTCPServer):
    """No-auth SOCKS5 CONNECT proxy that records every requested target.

    It only ever dials 127.0.0.1: loopback literals as asked, ``*.invalid``
    names on loopback at the requested port. Any other host is recorded and
    refused, so a test can assert the request arrived without real traffic.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__((_BIND_HOST, 0), _Socks5Handler)
        self.targets: list[tuple[str, int]] = []

    @property
    def port(self) -> int:
        return self.server_address[1]

    def url(self, scheme: str = "socks5h") -> str:
        return f"{scheme}://{_BIND_HOST}:{self.port}"


class _TargetHandler(BaseHTTPRequestHandler):
    server: "LocalHttpTarget"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        self.server.requests.append(("GET", path))
        route = self.server.routes.get(path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content_type, body = route
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalHttpTarget(ThreadingHTTPServer):
    """Plain HTTP server on 127.0.0.1 serving ``routes`` and recording requests."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__((_BIND_HOST, 0), _TargetHandler)
        self.routes: dict[str, tuple[str, bytes]] = {}
        self.requests: list[tuple[str, str]] = []

    @property
    def port(self) -> int:
        return self.server_address[1]

    def url(self, path: str, host: str = _BIND_HOST) -> str:
        return f"http://{host}:{self.port}{path}"


@contextmanager
def _serving(server: socketserver.BaseServer) -> Iterator[None]:
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def socks5_proxy() -> Iterator[Socks5ProxyServer]:
    """Start a recording SOCKS5 proxy on an ephemeral loopback port."""
    server = Socks5ProxyServer()
    with _serving(server):
        yield server


@pytest.fixture
def local_http_target() -> Iterator[LocalHttpTarget]:
    """Start a recording HTTP server on an ephemeral loopback port."""
    server = LocalHttpTarget()
    with _serving(server):
        yield server


@pytest.fixture
def local_livestream_target(local_http_target: LocalHttpTarget) -> LocalHttpTarget:
    """The HTTP target serving a one-segment, ended livestream at /master.m3u8."""
    playlist_type = "application/vnd.apple.mpegurl"
    local_http_target.routes["/master.m3u8"] = (
        playlist_type,
        build_master_playlist(variant_url="/variant.m3u8").encode(),
    )
    local_http_target.routes["/variant.m3u8"] = (
        playlist_type,
        build_variant_playlist(
            media_sequence=0, segment_uris=["seg0.ts"], endlist=True
        ).encode(),
    )
    local_http_target.routes["/seg0.ts"] = ("video/mp2t", DEFAULT_FAKE_TS_BYTES)
    return local_http_target


@pytest.fixture
def dead_proxy_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point every proxy environment variable at a closed loopback port."""
    with socket.socket() as probe:
        probe.bind((_BIND_HOST, 0))
        url = f"http://{_BIND_HOST}:{probe.getsockname()[1]}"
    for name in _PROXY_ENV_NAMES:
        monkeypatch.setenv(name, url)
        monkeypatch.setenv(name.lower(), url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    return url
