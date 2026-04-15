"""WebSocket event monitor — reads config.ini, connects, and logs all events.

Usage:
    poetry run python scripts/ws_monitor.py              # main event bus (wsv3)
    poetry run python scripts/ws_monitor.py --chat       # livestream chat (chatws)
    poetry run python scripts/ws_monitor.py --debug      # include per-message debug output
    poetry run python scripts/ws_monitor.py --config path/to/config.ini

Ctrl+C to stop. Events are logged to logs/ws_monitor.log and console.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from configparser import ConfigParser
from pathlib import Path

from loguru import logger


# ---------------------------------------------------------------------------
# Logging setup — must run BEFORE importing api.websocket, because
# config/logging.py calls logger.remove() at import time, leaving no
# handlers.  We add our own console + file handlers here.
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "ws_monitor.log"


def _setup_logging(debug: bool = False) -> None:
    """Configure loguru handlers for the monitor.

    Adds:
        1. Console (stderr) — coloured, shows [WS Monitor] lines in real time
        2. File (logs/ws_monitor.log) — timestamped, rotated at 50 MB
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = "DEBUG" if debug else "INFO"

    # Accept messages from textio_logger (used by api/websocket.py)
    def _textio_filter(record: dict) -> bool:
        return record["extra"].get("logger") == "textio"

    # Console handler
    logger.add(
        sys.stderr,
        format=(
            "<level>{level.name:>8}</level> | "
            "<white>{time:HH:mm:ss.SS}</white> | "
            "{message}"
        ),
        filter=_textio_filter,
        level=level,
        colorize=True,
    )

    # File handler — unique per-session via rotation
    logger.add(
        str(LOG_FILE),
        format="[{time:YYYY-MM-DD HH:mm:ss.SSS}] [{level.name:<8}] {message}",
        filter=_textio_filter,
        level=level,
        rotation="50 MB",
        retention=5,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _unscramble_token(token: str) -> str:
    """Unscramble a Fansly token if it has the scramble suffix."""
    scramble_suffix = "fNs"
    if not token.endswith(scramble_suffix):
        return token

    scrambled_token = token[: -len(scramble_suffix)]
    unscrambled_chars = [""] * len(scrambled_token)
    step_size = 7
    scrambled_index = 0

    for offset in range(step_size):
        for result_position in range(offset, len(unscrambled_chars), step_size):
            unscrambled_chars[result_position] = scrambled_token[scrambled_index]
            scrambled_index += 1

    return "".join(unscrambled_chars)


def _load_config(config_path: Path) -> tuple[str, str]:
    """Read token and user_agent from config.ini.

    Returns:
        (token, user_agent) tuple

    Raises:
        SystemExit: If config file or required fields are missing
    """
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    parser = ConfigParser(interpolation=None)
    parser.read(config_path)

    token = parser.get("MyAccount", "authorization_token", fallback="")
    user_agent = parser.get("MyAccount", "user_agent", fallback="")

    if not token:
        print("No authorization_token in config", file=sys.stderr)
        sys.exit(1)
    if not user_agent:
        print("No user_agent in config", file=sys.stderr)
        sys.exit(1)

    return _unscramble_token(token), user_agent


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


async def monitor(
    token: str,
    user_agent: str,
    base_url: str | None = None,
    debug: bool = False,
) -> None:
    """Connect to Fansly WebSocket and log events until interrupted."""
    # Import triggers config/logging.py which calls logger.remove(),
    # nuking any handlers added before this point.  We re-add ours after.
    from api.websocket import FanslyWebSocket

    _setup_logging(debug=debug)

    ws = FanslyWebSocket(
        token=token,
        user_agent=user_agent,
        monitor_events=True,
        enable_logging=debug,
        base_url=base_url,
    )

    await ws.start_background()

    url_label = base_url or ws.WEBSOCKET_URL
    logger.bind(logger="textio").info(
        "Monitoring {} — events log to {}", url_label, LOG_FILE
    )

    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        await ws.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Fansly WebSocket event monitor")
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Connect to chatws.fansly.com instead of wsv3",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Custom WebSocket URL (overrides --chat)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.ini"),
        help="Path to config.ini (default: ./config.ini)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable per-message debug logging",
    )
    args = parser.parse_args()

    token, user_agent = _load_config(args.config)

    base_url = args.url
    if base_url is None and args.chat:
        base_url = "wss://chatws.fansly.com"

    try:
        asyncio.run(monitor(token, user_agent, base_url=base_url, debug=args.debug))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
