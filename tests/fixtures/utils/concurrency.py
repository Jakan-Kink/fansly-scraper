"""Concurrency utilities for tests.

The primary export is :class:`SyncExecutor`, a synchronous drop-in for
:class:`concurrent.futures.ThreadPoolExecutor`. It's needed because
coverage.py doesn't track lines executed in threads by default — the
project isn't configured with ``concurrency = ["thread"]`` in
``pyproject.toml`` because that flag adds overhead for every test run.
Individual tests that need to exercise code inside a thread pool and
verify it via coverage can swap in ``SyncExecutor`` instead, at
monkeypatch-level, for the duration of the test.

Usage::

    from tests.fixtures.utils import SyncExecutor

    monkeypatch.setattr(
        "some.module.concurrent.futures.ThreadPoolExecutor", SyncExecutor
    )

Pattern match with production code using the executor:
- ``with ThreadPoolExecutor(...) as ex: ex.map(fn, *iters)`` → works as-is.
- ``ex.submit(fn)`` returning a Future → NOT supported; would need to
  extend ``SyncExecutor`` with a trivial Future-like wrapper. So far the
  ``.map``-only protocol has been sufficient for every caller.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any


class SyncExecutor:
    """Synchronous stand-in for ``concurrent.futures.ThreadPoolExecutor``.

    Implements only the minimal protocol production code typically uses:

    - Context manager (``__enter__``/``__exit__``)
    - ``.map(func, *iterables)`` — runs func eagerly + returns a generator

    If a caller ever uses ``executor.submit(...)``, this class will raise
    ``AttributeError`` and must be extended. Keeping it minimal on purpose
    — adding a Future shim before it's needed would hide API-shape drift.
    """

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self) -> SyncExecutor:
        return self

    def __exit__(self, *_args) -> bool:
        return False

    @staticmethod
    def map(func, *iterables) -> Generator[Any, None, None]:
        """Run ``func`` synchronously over zipped ``iterables``.

        Matches ``ThreadPoolExecutor.map``'s signature closely enough for
        the production patterns seen in this project. Uses
        ``zip(..., strict=False)`` to tolerate mismatched iterable lengths
        in the same way stdlib's ``map`` does.
        """
        return (func(*args) for args in zip(*iterables, strict=False))

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """No-op shutdown — required by asyncio.BaseEventLoop.close().

        When SyncExecutor is patched in for ThreadPoolExecutor and the
        asyncio loop happens to install it as its default executor, the
        loop's close() invokes ``executor.shutdown(wait=False)``.
        Without this method asyncio raises AttributeError during teardown.
        """


__all__ = ["SyncExecutor"]
