"""Example usage of FanslyWebSocket client.

This example demonstrates how to use the WebSocket client
in various patterns.
"""

import asyncio
from typing import Any

from api.websocket import FanslyWebSocket


async def example_background_task() -> None:
    """Example: Run WebSocket in background while doing other work."""

    # Your authentication token
    token = "your_token_here"
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0"

    # Optional: Provide cookies for better anti-detection
    cookies = {
        "f-s-c": "your_session_cookie",
        "f-d": "your_device_id",
        # Add other cookies as needed
    }

    # Create WebSocket client
    ws_client = FanslyWebSocket(
        token=token,
        user_agent=user_agent,
        cookies=cookies,
        enable_logging=True,  # Enable for debugging
    )

    # Start WebSocket in background
    await ws_client.start_background()

    print(f"WebSocket connected! Session ID: {ws_client.session_id}")
    print(f"Account ID: {ws_client.account_id}")
    print(f"WebSocket Session ID: {ws_client.websocket_session_id}")

    # Do your main work here...
    # The WebSocket stays connected in the background,
    # sending pings every 20 seconds

    print("Simulating main work for 60 seconds...")
    await asyncio.sleep(60)

    # Stop WebSocket when done
    await ws_client.stop()
    print("WebSocket disconnected")


async def example_context_manager() -> None:
    """Example: Use WebSocket with async context manager."""

    token = "your_token_here"
    user_agent = "Mozilla/5.0 ..."

    # WebSocket automatically starts and stops
    async with FanslyWebSocket(token, user_agent) as ws:
        print(f"Connected! Session: {ws.session_id}")

        # Do your work here
        await asyncio.sleep(30)

    # WebSocket automatically disconnected
    print("Done!")


async def example_with_custom_handler() -> None:
    """Example: Register custom event handler."""

    token = "your_token_here"
    user_agent = "Mozilla/5.0 ..."

    ws_client = FanslyWebSocket(token, user_agent, enable_logging=True)

    # Register handler for a custom message type (example: type 3)
    async def handle_notification(data: dict[str, Any]) -> None:
        """Handle notification events."""
        print(f"Received notification: {data}")

    ws_client.register_handler(3, handle_notification)

    # Start and run
    await ws_client.start_background()
    await asyncio.sleep(60)
    await ws_client.stop()


async def example_integration_with_api() -> None:
    """Example: Use WebSocket alongside FanslyApi."""

    from api.fansly import FanslyApi

    token = "your_token_here"
    user_agent = "Mozilla/5.0 ..."
    check_key = "your_check_key"

    # Initialize both API and WebSocket
    api = FanslyApi(
        token=token,
        user_agent=user_agent,
        check_key=check_key,
    )

    ws = FanslyWebSocket(token, user_agent)

    # Start WebSocket in background
    await ws.start_background()

    print("WebSocket connected for anti-detection")
    print(f"Session: {ws.session_id}")

    # Now use API for downloads and operations
    # while WebSocket maintains connection

    # Example: Setup session
    await api.setup_session()

    # Do your downloads...
    # api.get_timeline(...)
    # api.download_media(...)

    # When done, stop WebSocket
    await ws.stop()


if __name__ == "__main__":
    # Run one of the examples
    print("Running background task example...")
    asyncio.run(example_background_task())

    # Or run context manager example
    # asyncio.run(example_context_manager())

    # Or run with custom handler
    # asyncio.run(example_with_custom_handler())

    # Or run integration example
    # asyncio.run(example_integration_with_api())
