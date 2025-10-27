"""Monitor and manage POSIX semaphores."""

import inspect
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from config import trace_logger
from textio import print_warning


# Global tracking of seen semaphores
_seen_semaphores: dict[str, set[str]] = defaultdict(set)


class SemaphoreInfo(NamedTuple):
    """Information about a POSIX semaphore."""

    fd: int
    name: str
    pid: int

    def __hash__(self) -> int:
        return hash((self.fd, self.name, self.pid))


def _get_creation_stack() -> str:
    """Get a formatted stack trace for semaphore creation point."""
    stack = inspect.stack()[2:]  # Skip this function and caller
    # Format as module:function:line
    return " -> ".join(
        f"{frame.filename.split('/')[-1]}:{frame.function}:{frame.lineno}"
        for frame in stack[:3]  # Show last 3 frames
    )


def get_process_semaphores() -> list[SemaphoreInfo]:
    """Get list of POSIX semaphores for current process."""
    try:
        # Run lsof to get file descriptors
        # S607: Using standard system utility - full path would be less portable
        result = subprocess.run(
            ["lsof", "-p", str(os.getpid())],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print_warning(f"Failed to get semaphore list: {e}")
        return []
    else:
        # Parse output for PSXSEM entries
        semaphores = []
        for line in result.stdout.splitlines():
            if "PSXSEM" in line:
                # Parse lsof output format
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        fd = int(parts[3].rstrip("rwu"))  # Remove r/w/u flags
                        name = parts[-1]  # Last part is the name
                        pid = int(parts[1])
                        sem = SemaphoreInfo(fd, name, pid)
                        semaphores.append(sem)

                        # Track creation point if this is a new semaphore
                        if name not in _seen_semaphores:
                            _seen_semaphores[name].add(_get_creation_stack())

                    except (ValueError, IndexError):
                        continue

        return semaphores


def cleanup_semaphores(pattern: str | None = None) -> None:
    """Clean up POSIX semaphores matching pattern.

    Args:
        pattern: Optional regex pattern to match semaphore names.
               If None, attempts to clean up all semaphores.
    """
    semaphores = get_process_semaphores()
    if pattern:
        regex = re.compile(pattern)
        semaphores = [s for s in semaphores if regex.search(s.name)]

    for sem in semaphores:
        try:
            # Try to unlink the semaphore
            Path(sem.name).unlink(missing_ok=True)
        except OSError as e:
            print_warning(f"Failed to clean up semaphore {sem.name}: {e}")


def monitor_semaphores(threshold: int = 60) -> None:
    """Monitor number of POSIX semaphores and warn if above threshold.

    Args:
        threshold: Warning threshold for number of semaphores.
    """
    semaphores = get_process_semaphores()
    if len(semaphores) > threshold:
        print_warning(
            f"High number of POSIX semaphores: {len(semaphores)} (threshold: {threshold})"
        )
        trace_logger.trace("Semaphore list:")

        # Group semaphores by creation point for better visibility
        by_creation: dict[str, list[SemaphoreInfo]] = defaultdict(list)
        for sem in semaphores:
            creation_points = _seen_semaphores.get(sem.name, {"<unknown>"})
            for point in creation_points:
                by_creation[point].append(sem)

        # Print grouped by creation point
        for creation_point, sems in by_creation.items():
            trace_logger.trace(f"\nCreated at: {creation_point}")
            for sem in sems:
                trace_logger.trace(f"  FD: {sem.fd}, Name: {sem.name}")
