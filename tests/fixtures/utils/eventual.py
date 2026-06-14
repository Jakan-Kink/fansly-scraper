"""Eventual-consistency polling for live-server integration tests.

Stash is backed by SQLite. A burst of writes immediately followed by a read
(create gallery -> add images -> re-fetch) can race the write's visibility:
under WAL + busy-timeout pressure the fresh read connection occasionally sees
the pre-write state and returns zero rows. The write succeeded; the read was
simply too soon. ``poll_until`` retries a read a few times with a short delay so
the assertion reflects committed server state instead of a read-after-write
race.

Use ONLY for live-server reads that are legitimately eventually consistent —
never to paper over a real "the write never happened" bug. Pair it with a
timing-independent proof of the write itself (e.g. asserting the mutation fired
off the captured GraphQL wire).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def poll_until[T](
    read: Callable[[], Awaitable[T]],
    predicate: Callable[[T], bool],
    *,
    attempts: int = 5,
    delay: float = 0.2,
) -> T:
    """Call ``read`` until ``predicate`` holds, returning the last result.

    Args:
        read: Async no-arg callable performing the read (re-invoked each attempt).
        predicate: Returns True when the read result is acceptable.
        attempts: Maximum reads (>= 1). The first is immediate; each subsequent
            attempt is preceded by ``delay`` seconds.
        delay: Seconds to sleep between attempts (real sleep — integration use).

    Returns:
        The result of the first read that satisfies ``predicate``, or the final
        read's result if none did (caller asserts on it for a clear failure).
    """
    result = await read()
    for _ in range(max(0, attempts - 1)):
        if predicate(result):
            return result
        await asyncio.sleep(delay)
        result = await read()
    return result
