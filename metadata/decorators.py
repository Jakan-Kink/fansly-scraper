"""Decorators for metadata operations."""

import asyncio
import contextlib
import functools
import inspect
import random
import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.exc import OperationalError


# Lazy import to avoid circular dependency issues during alembic migrations
# textio functions are imported within methods that use them
# from textio import print_error, print_info, print_warning

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
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> RT:
            # Get the session parameter info from the function signature
            sig = inspect.signature(func)
            session_param = next(
                (
                    (name, param)
                    for name, param in sig.parameters.items()
                    if param.annotation in {"Session | None", "sqlalchemy.orm.Session | None"}
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
                try:
                    session_context = self.database.async_session_scope()

                    # Handle both async and non-async session scope methods
                    if asyncio.iscoroutine(session_context):
                        session = await session_context
                    else:
                        # If not a coroutine, it should support async context manager protocol
                        session = await stack.enter_async_context(session_context)

                    kwargs[session_name] = session
                    return await func(self, *args, **kwargs)
                except TypeError as e:
                    # If we encounter issues with the async context manager
                    if (
                        "does not support the asynchronous context manager protocol"
                        in str(e)
                    ):
                        from textio import print_error

                        print_error(f"Error in async context: {e}")
                        # Try to handle coroutine objects that don't support context manager
                        if hasattr(session_context, "__await__"):
                            session = await session_context
                            kwargs[session_name] = session
                            return await func(self, *args, **kwargs)
                        if hasattr(session_context, "session"):
                            # Last resort - try to access the session directly
                            kwargs[session_name] = session_context.session
                            return await func(self, *args, **kwargs)
                    # Re-raise other errors
                    raise

        return wrapper

    return decorator


def _get_retry_settings(func_name: str) -> tuple[int, float, float]:
    """Get default retry settings based on function name.

    Args:
        func_name: Name of the function being decorated

    Returns:
        Tuple of (retry_count, base_delay, max_delay)
    """
    if any(x in func_name for x in ["sync", "cleanup", "close"]):
        return 10, 1.0, 30.0  # More retries and longer delays for sync operations
    return 3, 0.1, 5.0  # Normal settings for regular operations


def _calculate_retry_delay(
    attempt: int, base_delay: float, max_delay: float, jitter: bool = True
) -> float:
    """Calculate delay for next retry attempt.

    Args:
        attempt: Current attempt number (0-based)
        base_delay: Base delay between retries
        max_delay: Maximum delay allowed
        jitter: Whether to add random jitter

    Returns:
        Delay in seconds for next retry
    """
    delay = min(max_delay, base_delay * (2**attempt))
    if jitter:
        delay *= random.uniform(0.5, 1.5)
    return delay


def _log_retry_attempt(
    func_name: str,
    attempt: int,
    retry_count: int,
    retry_delay: float,
    base_wait: float,
    wait_time: float,
    max_wait: float,
) -> None:
    """Log information about retry attempt.

    Args:
        func_name: Name of the function being retried
        attempt: Current attempt number (0-based)
        retry_count: Maximum number of retries
        retry_delay: Base delay between retries
        base_wait: Base wait time without jitter
        wait_time: Actual wait time with jitter
        max_wait: Maximum wait time allowed
    """
    from textio import print_info

    print_info(
        f"Database locked on {func_name}, attempt {attempt + 1}/{retry_count}\n"
        f"  Base delay: {retry_delay:.3f}s\n"
        f"  This attempt: {base_wait:.1f}s\n"
        f"  With jitter: {wait_time:.1f}s\n"
        f"  Max delay: {max_wait:.1f}s"
    )


async def _handle_async_retry(
    func: Callable[..., RT],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    retry_count: int,
    retry_delay: float,
    max_wait: float,
    jitter: bool,
) -> RT:
    """Handle retries for async functions.

    Args:
        func: Async function to retry
        args: Positional arguments for function
        kwargs: Keyword arguments for function
        retry_count: Maximum number of retries
        retry_delay: Base delay between retries
        max_wait: Maximum delay allowed
        jitter: Whether to add random jitter

    Returns:
        Result from function call

    Raises:
        Exception: If all retries fail
    """
    # Get function signature
    sig = inspect.signature(func)

    # Handle keyword-only arguments
    params = list(sig.parameters.values())
    if params and params[0].name == "self":
        # Skip self parameter for methods
        params = params[1:]

    # Check for keyword-only parameters
    keyword_only = {p.name for p in params if p.kind == inspect.Parameter.KEYWORD_ONLY}

    # If we have keyword-only parameters and they're in args,
    # move them to kwargs
    if keyword_only and args:
        bound = sig.bind_partial(*args)
        for name in keyword_only:
            if name in bound.arguments:
                kwargs[name] = bound.arguments[name]
                args = tuple(a for i, a in enumerate(args) if params[i].name != name)

    for attempt in range(retry_count):
        try:
            return await func(*args, **kwargs)
        except (sqlite3.OperationalError, OperationalError) as e:
            if "database is locked" not in str(e):
                raise
            if attempt == retry_count - 1:
                from textio import print_error

                print_error(f"Max retries ({retry_count}) reached for {func.__name__}")
                raise

            base_wait = retry_delay * (2**attempt)
            wait_time = _calculate_retry_delay(attempt, retry_delay, max_wait, jitter)
            _log_retry_attempt(
                func.__name__,
                attempt,
                retry_count,
                retry_delay,
                base_wait,
                wait_time,
                max_wait,
            )
            await asyncio.sleep(wait_time)

    return None  # type: ignore # Typing hint - never reached


def _handle_sync_retry(
    func: Callable[..., RT],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    retry_count: int,
    retry_delay: float,
    max_wait: float,
    jitter: bool,
) -> RT:
    """Handle retries for sync functions.

    Args:
        func: Sync function to retry
        args: Positional arguments for function
        kwargs: Keyword arguments for function
        retry_count: Maximum number of retries
        retry_delay: Base delay between retries
        max_wait: Maximum delay allowed
        jitter: Whether to add random jitter

    Returns:
        Result from function call

    Raises:
        Exception: If all retries fail
    """
    # Get function signature
    sig = inspect.signature(func)

    # Handle keyword-only arguments
    params = list(sig.parameters.values())
    if params and params[0].name == "self":
        # Skip self parameter for methods
        params = params[1:]

    # Check for keyword-only parameters
    keyword_only = {p.name for p in params if p.kind == inspect.Parameter.KEYWORD_ONLY}

    # If we have keyword-only parameters and they're in args,
    # move them to kwargs
    if keyword_only and args:
        bound = sig.bind_partial(*args)
        for name in keyword_only:
            if name in bound.arguments:
                kwargs[name] = bound.arguments[name]
                args = tuple(a for i, a in enumerate(args) if params[i].name != name)

    for attempt in range(retry_count):
        try:
            return func(*args, **kwargs)
        except (sqlite3.OperationalError, OperationalError) as e:
            if "database is locked" not in str(e):
                raise
            if attempt == retry_count - 1:
                from textio import print_error

                print_error(f"Max retries ({retry_count}) reached for {func.__name__}")
                raise

            base_wait = retry_delay * (2**attempt)
            wait_time = _calculate_retry_delay(attempt, retry_delay, max_wait, jitter)
            _log_retry_attempt(
                func.__name__,
                attempt,
                retry_count,
                retry_delay,
                base_wait,
                wait_time,
                max_wait,
            )
            time.sleep(wait_time)

    return None  # type: ignore # Typing hint - never reached


def retry_on_locked_db(
    retries: int | None = None,
    delay: float | None = None,
    max_delay: float | None = None,
    jitter: bool = True,
) -> Callable[[Callable[..., RT]], Callable[..., RT]]:
    """Decorator to retry operations when database is locked.

    Args:
        retries: Number of retries (default: 3 for normal ops, 10 for sync)
        delay: Base delay between retries in seconds
            (default: 0.1 for normal ops, 1.0 for sync)
        max_delay: Maximum delay between retries in seconds
            (default: 5.0 for normal ops, 30.0 for sync)
        jitter: Whether to add random jitter to delay (default: True)
            Helps prevent thundering herd problems

    Returns:
        Decorated function that handles retries
    """

    def decorator(func: Callable[..., RT]) -> Callable[..., RT]:
        # Get retry settings
        retry_count, retry_delay, max_wait = _get_retry_settings(func.__name__)
        if retries is not None:
            retry_count = retries
        if delay is not None:
            retry_delay = delay
        if max_delay is not None:
            max_wait = max_delay

        # Get original function (unwrap any other decorators)
        original_func = inspect.unwrap(func)
        sig = inspect.signature(original_func)

        # Create wrapper with same signature
        if asyncio.iscoroutinefunction(func):

            async def async_wrapper(*args: Any, **kwargs: Any) -> RT:
                # Try to bind arguments to original signature
                try:
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    args = bound.args
                    kwargs = bound.kwargs
                except TypeError:
                    # If binding fails, pass through as-is
                    pass

                return await _handle_async_retry(
                    func, args, kwargs, retry_count, retry_delay, max_wait, jitter
                )

            # Copy signature to wrapper
            async_wrapper.__signature__ = sig  # type: ignore
            return functools.wraps(func)(async_wrapper)

        def sync_wrapper(*args: Any, **kwargs: Any) -> RT:
            # Try to bind arguments to original signature
            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                args = bound.args
                kwargs = bound.kwargs
            except TypeError:
                # If binding fails, pass through as-is
                pass

            return _handle_sync_retry(
                func, args, kwargs, retry_count, retry_delay, max_wait, jitter
            )

        # Copy signature to wrapper
        sync_wrapper.__signature__ = sig  # type: ignore
        return functools.wraps(func)(sync_wrapper)

    return decorator
