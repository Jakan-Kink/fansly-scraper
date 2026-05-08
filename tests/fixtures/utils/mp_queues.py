"""multiprocessing.Queue cleanup utility."""

from __future__ import annotations

import contextlib
from typing import Any


def close_qs(*qs: Any) -> None:
    """Drain, close, and join feeder threads on each multiprocessing.Queue."""
    # Daemon feeder thread races _Py_Finalize without explicit join → SIGABRT.
    for q in qs:
        if q is None:
            continue
        with contextlib.suppress(Exception):
            while True:
                q.get_nowait()
        with contextlib.suppress(Exception):
            q.close()
        with contextlib.suppress(Exception):
            q.join_thread()
