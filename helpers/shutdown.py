"""Stop flag for work running in worker threads."""

import threading


# A worker thread cannot be cancelled, and the event loop waits for it.
_stop = threading.Event()


class StopRequested(BaseException):
    """Ends work in a worker thread; passes through ``except Exception``."""


def request_stop() -> None:
    """Make work in worker threads end at its next check."""
    _stop.set()


def stop_requested() -> bool:
    """Whether worker threads were asked to stop."""
    return _stop.is_set()


def raise_if_stopping() -> None:
    """Raise ``StopRequested`` once a stop was requested."""
    if _stop.is_set():
        raise StopRequested("Stopped for shutdown.")
