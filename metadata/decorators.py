"""Decorators for metadata operations."""

import contextlib
import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.orm import Session

RT = TypeVar("RT")


def with_session() -> Callable[[Callable[..., RT]], Callable[..., RT]]:
    """Decorator to handle optional session parameters.

    This decorator:
    1. Checks if a session is provided in the function arguments
    2. If not, creates a new session using self.database
    3. Ensures proper session cleanup after function execution

    Usage:
        @with_session()
        async def my_func(self, ..., session: Session | None = None):
            # session is guaranteed to be available here
            ...

    Returns:
        Decorated function that handles session management
    """

    def decorator(func: Callable[..., RT]) -> Callable[..., RT]:
        @functools.wraps(func)
        async def wrapper(self, *args: Any, **kwargs: Any) -> RT:
            # Get the session parameter name from the function signature
            sig = inspect.signature(func)
            session_param = next(
                (
                    name
                    for name, param in sig.parameters.items()
                    if param.annotation == "Session | None"
                    or param.annotation == "sqlalchemy.orm.Session | None"
                ),
                "session",
            )

            # Check if session is already provided
            if session_param in kwargs and kwargs[session_param] is not None:
                return await func(self, *args, **kwargs)

            # Create new session using self.database
            async with contextlib.AsyncExitStack() as stack:
                session = await stack.enter_async_context(
                    self.database.get_async_session()
                )
                kwargs[session_param] = session
                return await func(self, *args, **kwargs)

        return wrapper

    return decorator
