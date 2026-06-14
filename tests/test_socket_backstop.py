"""Self-verifying guard for the pytest-socket network backstop.

The suite runs with ``--disable-socket --allow-hosts=127.0.0.1,::1
--allow-unix-socket`` (pyproject ``addopts``): unit tests are respx-mocked and
open no real socket, while integration tests legitimately reach only localhost
(Postgres :5432, Stash :9999/:5000). A leaked external call (a missing respx
mock falling through to real Fansly/CDN HTTP) hits a non-localhost socket and
fails loudly. These tests fail if that config is ever dropped.
"""

import socket

import pytest
from pytest_socket import SocketConnectBlockedError


def test_external_host_is_blocked():
    """A non-localhost connection is refused by the backstop, never reaches the wire.

    pytest-socket raises before any packet is sent, so 8.8.8.8 is never actually
    contacted — it is just a guaranteed non-whitelisted address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(SocketConnectBlockedError):
            sock.connect(("8.8.8.8", 80))
    finally:
        sock.close()


def test_localhost_is_allowed():
    """localhost is whitelisted — a connect to a closed port refuses, not blocks.

    A ``ConnectionRefusedError`` (or timeout) means the connect was *attempted*
    (host allowed); a ``SocketConnectBlockedError`` would mean localhost was
    wrongly blocked.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", 1))
    except SocketConnectBlockedError:  # pragma: no cover - the failure we guard against
        pytest.fail("localhost must be allowed by the backstop")
    except OSError:
        pass  # refused / unreachable / timeout — the connect was allowed through
    finally:
        sock.close()
