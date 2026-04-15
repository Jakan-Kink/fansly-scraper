"""Standalone WebSocket event monitor for Fansly protocol discovery.

Connects directly using the websockets library — no dependency on the
api package, avoiding the circular import chain.

Usage:
    poetry run python scripts/ws_monitor.py              # main event bus (wsv3)
    poetry run python scripts/ws_monitor.py --chat       # livestream chat (chatws)
    poetry run python scripts/ws_monitor.py -v           # debug logging (no ping/ack)
    poetry run python scripts/ws_monitor.py -vv          # extra verbose (ping/ack too)
    poetry run python scripts/ws_monitor.py --config path/to/config.ini

Ctrl+C to stop. Events are logged to logs/ws_monitor.log and console.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import ssl
import sys
from configparser import ConfigParser
from pathlib import Path

import websockets
from loguru import logger
from websockets.exceptions import WebSocketException

# ---------------------------------------------------------------------------
# Protocol constants (from main.js EventService.handleText)
# ---------------------------------------------------------------------------

MSG_ERROR = 0  # ErrorEvent — server error with code
MSG_SESSION = 1  # SessionVerifyRequest / SessionVerifiedEvent
MSG_PING = 2  # PingResponseEvent — response to "p"
MSG_SERVICE_EVENT = 10000  # ServiceEvent — real-time notifications
MSG_BATCH = 10001  # Batch — array of messages, recursively unpacked

# Known service IDs within ServiceEvent (from observed traffic)
SVC_POST = 1  # Post interactions (likes, etc.)
SVC_MEDIA = 2  # Media/content interactions (likes, etc.)
SVC_FOLLOWS = 3  # Follow/unfollow events
SVC_MESSAGING = 4  # Message delivery and acknowledgments
SVC_MSG_INTERACT = 5  # Message interactions (new messages, likes)
SVC_WALLET = 6  # Wallet balance updates and transactions
SVC_SUBSCRIPTIONS = 15  # Subscription lifecycle (created → confirmed)
SVC_PAYMENTS = 16  # External payment processing (card charges, 3DS)
SVC_POLLS = 42  # Poll viewport subscriptions (auto sub/unsub on scroll)

WS_VERSION = 3
DEFAULT_URL = "wss://wsv3.fansly.com"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "ws_monitor.log"


def _setup_logging(verbosity: int = 0) -> None:
    """Configure loguru with console + file handlers.

    Args:
        verbosity: 0=INFO, 1=DEBUG (no ping/ack), 2=DEBUG (all events)
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = "DEBUG" if verbosity >= 1 else "INFO"

    # Console
    logger.add(
        sys.stderr,
        format=(
            "<level>{level.name:>8}</level> | "
            "<white>{time:HH:mm:ss.SS}</white> | "
            "{message}"
        ),
        level=level,
        colorize=True,
    )

    # File — rotated at 50 MB
    logger.add(
        str(LOG_FILE),
        format="[{time:YYYY-MM-DD HH:mm:ss.SSS}] [{level.name:<8}] {message}",
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
    """Read token and user_agent from config.ini."""
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
# Event monitor — decode and categorize
# ---------------------------------------------------------------------------


def _handle_message(raw: str | bytes, *, verbosity: int = 0) -> None:
    """Decode and log a WebSocket message.

    Args:
        raw: Raw message from WebSocket
        verbosity: 0=normal, 1=debug (skip ping/ack), 2=all events
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        data = json.loads(raw)
        message_type = data.get("t")
        message_data = data.get("d")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("[Monitor] Decode error: {}", e)
        return

    # Ping — only show at -vv
    if message_type == MSG_PING:
        if verbosity >= 2:
            logger.debug("[Monitor] Ping response")
        return

    if message_type == MSG_ERROR:
        error = (
            json.loads(message_data)
            if isinstance(message_data, str)
            else (message_data or {})
        )
        logger.info("[Monitor] Error | code={}", error.get("code"))
        return

    if message_type == MSG_SESSION:
        session = (
            json.loads(message_data)
            if isinstance(message_data, str)
            else (message_data or {})
        )
        sess = session.get("session", {})
        logger.info(
            "[Monitor] Session | id={} wsId={} account={} status={}",
            sess.get("id"),
            sess.get("websocketSessionId"),
            sess.get("accountId"),
            sess.get("status"),
        )
        return

    if message_type == MSG_SERVICE_EVENT:
        _handle_service_event(message_data, verbosity=verbosity)
        return

    if message_type == MSG_BATCH:
        batch = message_data if isinstance(message_data, list) else []
        logger.debug("[Monitor] Batch of {} events", len(batch))
        for sub in batch:
            _handle_message(
                json.dumps(sub) if isinstance(sub, dict) else sub,
                verbosity=verbosity,
            )
        return

    # Unknown top-level message type — full dump
    logger.info(
        "[Monitor] Unknown t={}\n{}",
        message_type,
        json.dumps(message_data, indent=2) if message_data else "(empty)",
    )


def _handle_service_event(message_data: str, *, verbosity: int = 0) -> None:
    """Decode and categorize a ServiceEvent (type 10000)."""
    try:
        envelope = (
            json.loads(message_data)
            if isinstance(message_data, str)
            else (message_data or {})
        )
    except json.JSONDecodeError:
        logger.warning("[Monitor] ServiceEvent decode error: {}", message_data)
        return

    service_id = envelope.get("serviceId")
    raw_event = envelope.get("event")

    try:
        event = (
            json.loads(raw_event) if isinstance(raw_event, str) else (raw_event or {})
        )
    except json.JSONDecodeError:
        logger.warning(
            "[Monitor] ServiceEvent inner decode error | svc={}: {}",
            service_id,
            raw_event,
        )
        return

    event_type = event.get("type")

    # Messaging ACK/read-receipt events (serviceId=4) — only at -vv
    if service_id == SVC_MESSAGING:
        if verbosity >= 2:
            ack = event.get("ackCommand", {})
            logger.debug(
                "[Monitor] ACK | group={} msgs={} user={}",
                ack.get("groupId"),
                len(ack.get("messageIds", [])),
                ack.get("userId"),
            )
        return

    # --- Known service categorization ---

    # Post interactions (serviceId=1)
    if service_id == SVC_POST:
        if "like" in event:
            like = event["like"]
            logger.info(
                "[Monitor] Post Like | post={} account={} | id={}",
                like.get("postId"),
                like.get("accountId"),
                like.get("id"),
            )
            return

    # Media interactions (serviceId=2)
    if service_id == SVC_MEDIA:
        if "like" in event:
            like = event["like"]
            logger.info(
                "[Monitor] Media Like | media={} bundle={} access={} | id={} at={}",
                like.get("accountMediaId"),
                like.get("accountMediaBundleId"),
                like.get("accountMediaAccess"),
                like.get("id"),
                like.get("createdAt"),
            )
            return

    # Message interactions (serviceId=5)
    if service_id == SVC_MSG_INTERACT:
        if "message" in event:
            msg = event["message"]
            content = msg.get("content", "")
            preview = (content[:60] + "...") if len(content) > 60 else content
            attachments = msg.get("attachments", [])
            logger.info(
                "[Monitor] New Message | from={} group={} attachments={} | id={}"
                ' content="{}"',
                msg.get("senderId"),
                msg.get("groupId"),
                len(attachments),
                msg.get("id"),
                preview,
            )
            return

        if "like" in event:
            like = event["like"]
            logger.info(
                "[Monitor] Message Like | msg={} group={} like_type={} | id={}",
                like.get("messageId"),
                like.get("groupId"),
                like.get("type"),
                like.get("id"),
            )
            return

    # Follow events (serviceId=3)
    if service_id == SVC_FOLLOWS:
        if "follow" in event:
            follow = event["follow"]
            logger.info(
                "[Monitor] Follow | account={} follower={} | id={}",
                follow.get("accountId"),
                follow.get("followerId"),
                follow.get("id"),
            )
            return

    # Wallet events (serviceId=6)
    if service_id == SVC_WALLET:
        if "wallet" in event:
            w = event["wallet"]
            logger.info(
                "[Monitor] Wallet | balance={} version={} | id={}",
                w.get("balance"),
                w.get("walletVersion"),
                w.get("id"),
            )
            return
        if "transaction" in event:
            tx = event["transaction"]
            logger.info(
                "[Monitor] Wallet Tx | type={} amount={} status={} | id={} corr={}",
                tx.get("type"),
                tx.get("amount"),
                tx.get("status"),
                tx.get("id"),
                tx.get("correlationId"),
            )
            return

    # Subscription events (serviceId=15)
    if service_id == SVC_SUBSCRIPTIONS:
        if "subscription" in event:
            sub = event["subscription"]
            tier = sub.get("subscriptionTierName", "")
            label = f' "{tier}"' if tier else ""
            logger.info(
                "[Monitor] Subscription | account={} status={}{} price={} | id={}",
                sub.get("accountId"),
                sub.get("status"),
                label,
                sub.get("price"),
                sub.get("id"),
            )
            return

    # Payment events (serviceId=16)
    if service_id == SVC_PAYMENTS:
        if "transaction" in event:
            tx = event["transaction"]
            logger.info(
                "[Monitor] Payment | type={} amount={} status={} 3ds={} | id={}",
                tx.get("type"),
                tx.get("amount"),
                tx.get("status"),
                tx.get("threeDSecure"),
                tx.get("id"),
            )
            return

    # Poll viewport subscriptions (serviceId=42) — noise, hide at default
    if service_id == SVC_POLLS:
        if verbosity >= 2:
            ps = event.get("pollSubscription", {})
            action = "Sub" if event_type == 20 else "Unsub"
            logger.debug(
                "[Monitor] Poll {} | poll={} | id={}",
                action,
                ps.get("pollId"),
                ps.get("id"),
            )
        return

    # --- Unknown or uncategorized — full dump for discovery ---
    logger.info(
        "[Monitor] serviceId={} type={}\n{}",
        service_id,
        event_type,
        json.dumps(event, indent=2),
    )


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


async def monitor(
    token: str,
    user_agent: str,
    base_url: str = DEFAULT_URL,
    verbosity: int = 0,
) -> None:
    """Connect to Fansly WebSocket and log events until interrupted."""
    url = f"{base_url}/?v={WS_VERSION}"
    logger.info("Connecting to {} — events log to {}", url, LOG_FILE)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "User-Agent": user_agent,
        "Origin": "https://fansly.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Site": "same-site",
        "DNT": "1",
        "Sec-GPC": "1",
    }

    ws = await websockets.connect(uri=url, additional_headers=headers, ssl=ssl_ctx)

    # Authenticate
    auth_msg = json.dumps(
        {
            "t": MSG_SESSION,
            "d": json.dumps({"token": token, "v": WS_VERSION}),
        }
    )
    await ws.send(auth_msg)
    response = await ws.recv()
    _handle_message(response, verbosity=verbosity)

    # Ping loop — send "p" every 20-25s (jittered, matching browser behavior)
    async def ping_loop() -> None:
        try:
            while True:
                interval = random.uniform(20.0, 25.0)  # noqa: S311
                await asyncio.sleep(interval)
                await ws.send("p")
                if verbosity >= 2:
                    logger.debug("[Monitor] Sent ping (next in {:.1f}s)", interval)
        except (asyncio.CancelledError, WebSocketException):
            pass

    ping_task = asyncio.create_task(ping_loop())

    # Listen
    try:
        async for message in ws:
            _handle_message(message, verbosity=verbosity)
    except WebSocketException as e:
        logger.error("[Monitor] Connection error: {}", e)
    except asyncio.CancelledError:
        pass
    finally:
        ping_task.cancel()
        await ws.close()
        logger.info("[Monitor] Disconnected")


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
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v for debug logging, -vv to include ping/ack events",
    )
    args = parser.parse_args()

    _setup_logging(verbosity=args.verbose)
    token, user_agent = _load_config(args.config)

    base_url = args.url or ("wss://chatws.fansly.com" if args.chat else DEFAULT_URL)

    try:
        asyncio.run(
            monitor(token, user_agent, base_url=base_url, verbosity=args.verbose)
        )
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
