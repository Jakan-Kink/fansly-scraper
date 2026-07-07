"""Scaled-sleep accelerators — real await, scaled-to-zero duration.

Drop-in replacements for the ``AsyncMock(return_value=None)`` /
``MagicMock`` no-op sleep patches scattered across the suite. Unlike a no-op
mock, these preserve the real behaviour shape: the ``await``/call still happens
(so loop scheduling, ordering, and cancellation are unchanged), but the
requested duration is ignored so the suite stays fast.

Two flavours:

- ``scaled_async_sleep`` / ``scaled_sync_sleep`` — stateless drop-ins. Patch a
  module-local sleep binding to one of these instead of re-creating an
  ``AsyncMock``::

      monkeypatch.setattr("download.timeline.sleep", scaled_async_sleep)

- ``scaled_async_sleep_recording`` / ``scaled_sync_sleep_recording`` fixtures —
  yield a recorder whose ``.calls`` list captures every requested duration, for
  tests that assert "slept N seconds"::

      def test_x(scaled_sync_sleep_recording):
          monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
          ...
          assert scaled_sync_sleep_recording.calls == [3.0]
"""

from __future__ import annotations

import asyncio

import pytest


# Bind the real ``asyncio.sleep`` at import time. A test that patches
# ``some_module.asyncio.sleep`` mutates the global ``asyncio`` module object;
# if the accelerators looked up ``asyncio.sleep`` dynamically they would call
# *themselves* and recurse infinitely. Capturing the genuine coroutine here
# keeps the real ~0 await regardless of how the target binding is patched.
_real_async_sleep = asyncio.sleep


async def scaled_async_sleep(
    duration: float = 0, *args: object, **kwargs: object
) -> None:
    """Stand in for ``asyncio.sleep``/a module ``sleep``: real yield, ~0 wait."""
    await _real_async_sleep(0)


def scaled_sync_sleep(duration: float = 0, *args: object, **kwargs: object) -> None:
    """Stand in for ``time.sleep``: returns immediately."""
    return


class _RecordingAsyncSleep:
    """Async sleep stand-in that records requested durations, awaits ~0."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(
        self, duration: float = 0, *args: object, **kwargs: object
    ) -> None:
        self.calls.append(duration)
        await _real_async_sleep(0)


class _RecordingSyncSleep:
    """Sync sleep stand-in that records requested durations, returns at once."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, duration: float = 0, *args: object, **kwargs: object) -> None:
        self.calls.append(duration)


@pytest.fixture
def scaled_async_sleep_recording() -> _RecordingAsyncSleep:
    """Recorder stand-in for an async sleep; assert on ``.calls``."""
    return _RecordingAsyncSleep()


@pytest.fixture
def scaled_sync_sleep_recording() -> _RecordingSyncSleep:
    """Recorder stand-in for a sync sleep; assert on ``.calls``."""
    return _RecordingSyncSleep()
