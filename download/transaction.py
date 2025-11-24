"""Transaction utilities for download operations."""

from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def in_transaction_or_new[T](
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

    if session.in_transaction():
        # We're already in a transaction, create a savepoint
        if debug and "print_debug" in globals():
            globals()["print_debug"](f"Creating savepoint for {operation_name}")
        # NOTE: Do not call session.commit() inside session.begin() or session.begin_nested()
        # context managers. The context manager handles commit automatically on exit.
        # Calling commit() explicitly inside causes "Can't operate on closed transaction" error.
        # See: https://github.com/sqlalchemy/sqlalchemy/issues/6288
        #      https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
        async with session.begin_nested():
            results = await func(*args, **kwargs)
            await session.flush()
            return results
    else:
        # No transaction yet - execute directly and let outer context handle transaction
        # This allows async_session_scope() to manage the transaction lifecycle
        if debug and "print_debug" in globals():
            globals()["print_debug"](
                f"No transaction for {operation_name}, executing directly"
            )
        results = await func(*args, **kwargs)
        await session.flush()
        return results
