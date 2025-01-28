"""Stash processing event loop fixes."""

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

T = TypeVar("T")


def ensure_event_loop(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to ensure a function has access to an event loop.

    If no event loop is running, creates a new one for the function.
    If a loop is already running, uses that loop.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return func(*args, **kwargs)

    return wrapper


def run_coroutine_threadsafe(coro: Any) -> Any:
    """Run a coroutine in a way that's safe even if an event loop is running.

    If no loop is running, runs the coroutine directly.
    If a loop is running, schedules the coroutine as a task.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, schedule as task
            return asyncio.create_task(coro)
        else:
            # If loop exists but not running, run directly
            return loop.run_until_complete(coro)
    except RuntimeError:
        # If no loop exists, create one and run
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
