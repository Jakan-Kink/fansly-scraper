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
            # Get the session parameter info from the function signature
            sig = inspect.signature(func)
            session_param = next(
                (
                    (name, param)
                    for name, param in sig.parameters.items()
                    if param.annotation == "Session | None"
                    or param.annotation == "sqlalchemy.orm.Session | None"
                ),
                ("session", sig.parameters.get("session")),
            )
            session_name, param_info = session_param

            # Find any existing session value
            session_value = None
            if session_name in kwargs:
                session_value = kwargs[session_name]
            elif param_info and param_info.kind == param_info.POSITIONAL_OR_KEYWORD:
                # Check if we have a positional arg for the session
                arg_index = (
                    list(sig.parameters.keys()).index(session_name) - 1
                )  # -1 for self
                if arg_index < len(args):
                    session_value = args[arg_index]
                    args = (
                        args[:arg_index] + args[arg_index + 1 :]
                    )  # Remove session from args

            # If we have a valid session, use it
            if session_value is not None:
                kwargs[session_name] = session_value
                return await func(self, *args, **kwargs)

            # Create new session using self.database
            async with contextlib.AsyncExitStack() as stack:
                session = await stack.enter_async_context(
                    self.database.get_async_session()
                )
                kwargs[session_name] = session
                return await func(self, *args, **kwargs)

        return wrapper

    return decorator
