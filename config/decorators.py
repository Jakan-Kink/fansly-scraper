from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any, TypeVar


RT = TypeVar("RT")


def with_database_session(
    async_session: bool = False,
) -> Callable[[Callable[..., RT]], Callable[..., RT]]:
    """Decorator to handle database session management.

    Provides a session using config._database to the decorated function.
    If session already provided in kwargs, uses that instead.

    Args:
        async_session: Whether to use async session

    Examples:
        @with_database_session()
        def my_func(config: FanslyConfig, session: Session | None = None):
            # session is guaranteed to be available here
            ...

        @with_database_session(async_session=True)
        async def my_async_func(config: FanslyConfig, session: AsyncSession | None = None):
            # session is guaranteed to be available here
            ...
    """

    def decorator(func: Callable[..., RT]) -> Callable[..., RT]:
        # Check for session type mismatch
        is_async = asyncio.iscoroutinefunction(func)
        if async_session != is_async:
            raise ValueError(
                f"Session type mismatch: async_session={async_session} "
                f"but function is{' not' if async_session else ''} async"
            )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> RT:
            # Use existing session if provided
            if "session" in kwargs and kwargs["session"] is not None:
                return await func(*args, **kwargs)

            # Get config from args/kwargs
            config = kwargs.get("config")
            if config is None:
                for arg in args:
                    if hasattr(arg, "_database"):
                        config = arg
                        break
            if config is None:
                raise ValueError("Database configuration not found in args/kwargs")

            async with config._database.async_session_scope() as session:
                kwargs["session"] = session
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> RT:
            # Use existing session if provided
            if "session" in kwargs and kwargs["session"] is not None:
                return func(*args, **kwargs)

            # Get config from args/kwargs
            config = kwargs.get("config")
            if config is None:
                for arg in args:
                    if hasattr(arg, "_database"):
                        config = arg
                        break
            if config is None:
                raise ValueError("Database configuration not found in args/kwargs")

            with config._database.session_scope() as session:
                kwargs["session"] = session
                return func(*args, **kwargs)

        return async_wrapper if async_session else sync_wrapper

    return decorator
