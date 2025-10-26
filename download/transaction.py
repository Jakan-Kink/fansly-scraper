"""Transaction utilities for download operations."""

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from errors import DuplicatePageError

# Type variable for the transaction helper
T = TypeVar("T")


async def in_transaction_or_new(
    session: AsyncSession,
    func: Callable[..., Coroutine[Any, Any, T]],
    debug: bool = False,
    operation_name: str = "database operation",
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute a function in a transaction, creating a savepoint if already in a transaction.

    Args:
        session: The database session
        func: The async function to execute
        debug: Whether to print debug information
        operation_name: Name of the operation for debug messages
        *args: Positional arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function

    Raises:
        DuplicatePageError: Re-raised to be handled by the caller
    """
    # Check if 'session' is already in kwargs to avoid passing it twice
    if "session" not in kwargs:
        kwargs["session"] = session

    try:
        if session.in_transaction():
            # We're already in a transaction, create a savepoint
            if debug and "print_debug" in globals():
                globals()["print_debug"](f"Creating savepoint for {operation_name}")
            async with session.begin_nested():
                results = await func(*args, **kwargs)
                await session.flush()
                await session.commit()
                return results
        else:
            # Start a new transaction
            if debug and "print_debug" in globals():
                globals()["print_debug"](
                    f"Starting new transaction for {operation_name}"
                )
            async with session.begin():
                results = await func(*args, **kwargs)
                await session.flush()
                await session.commit()
                return results
    except DuplicatePageError:
        # Let DuplicatePageError propagate to be handled by the caller
        raise
