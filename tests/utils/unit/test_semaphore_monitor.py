"""Tests for utils/semaphore_monitor.py — POSIX semaphore monitoring utilities."""

import logging
import subprocess
from unittest.mock import patch

import pytest

from utils.semaphore_monitor import (
    SemaphoreInfo,
    _seen_semaphores,
    cleanup_semaphores,
    get_process_semaphores,
    monitor_semaphores,
)


def _warning_messages(caplog) -> list[str]:
    """Return WARNING-level messages from caplog (loguru → pytest-loguru bridge)."""
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def _trace_messages(caplog) -> list[str]:
    """Return TRACE-level messages from caplog.

    loguru's TRACE level is 5; pytest-loguru forwards records through the
    stdlib LogRecord protocol with levelname="TRACE" preserved.
    """
    return [r.getMessage() for r in caplog.records if r.levelname == "TRACE"]


@pytest.fixture(autouse=True)
def _clear_seen_semaphores():
    """Clear the global _seen_semaphores between tests."""
    _seen_semaphores.clear()
    yield
    _seen_semaphores.clear()


# -- SemaphoreInfo NamedTuple --


class TestSemaphoreInfo:
    def test_creation_and_fields(self):
        info = SemaphoreInfo(fd=5, name="/sem.test", pid=1234)
        assert info.fd == 5
        assert info.name == "/sem.test"
        assert info.pid == 1234

    def test_hash_deterministic(self):
        a = SemaphoreInfo(fd=5, name="/sem.test", pid=1234)
        b = SemaphoreInfo(fd=5, name="/sem.test", pid=1234)
        assert hash(a) == hash(b)
        assert a == b

    def test_hash_differs_for_different_values(self):
        a = SemaphoreInfo(fd=5, name="/sem.a", pid=100)
        b = SemaphoreInfo(fd=6, name="/sem.b", pid=200)
        # Can't guarantee different hashes, but can put in a set
        assert {a, b} == {a, b}
        assert len({a, b}) == 2


# -- get_process_semaphores --


LSOF_OUTPUT_WITH_SEMAPHORES = """\
COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME
python  12345  shawn    5u   PSXSEM              0      /sem.worker1
python  12345  shawn    6u   PSXSEM              0      /sem.worker2
python  12345  shawn    7r      REG  1,18     1234 5678 /some/file
"""

LSOF_OUTPUT_NO_SEMAPHORES = """\
COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME
python  12345  shawn    7r      REG  1,18     1234 5678 /some/file
"""


class TestGetProcessSemaphores:
    def test_parses_semaphores_from_lsof_output(self):
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=LSOF_OUTPUT_WITH_SEMAPHORES, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            sems = get_process_semaphores()

        assert len(sems) == 2
        assert sems[0].fd == 5
        assert sems[0].name == "/sem.worker1"
        assert sems[0].pid == 12345
        assert sems[1].fd == 6
        assert sems[1].name == "/sem.worker2"

    def test_returns_empty_when_no_semaphores(self):
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=LSOF_OUTPUT_NO_SEMAPHORES, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            sems = get_process_semaphores()

        assert sems == []

    def test_returns_empty_when_lsof_not_found(self):
        with patch("utils.semaphore_monitor.shutil.which", return_value=None):
            sems = get_process_semaphores()

        assert sems == []

    def test_returns_empty_on_subprocess_error(self):
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch(
                "utils.semaphore_monitor.subprocess.run",
                side_effect=subprocess.SubprocessError("lsof failed"),
            ),
        ):
            sems = get_process_semaphores()

        assert sems == []

    def test_returns_empty_on_os_error(self):
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch(
                "utils.semaphore_monitor.subprocess.run",
                side_effect=OSError("Permission denied"),
            ),
        ):
            sems = get_process_semaphores()

        assert sems == []

    def test_tracks_new_semaphores_in_seen_set(self):
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=LSOF_OUTPUT_WITH_SEMAPHORES, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            get_process_semaphores()

        assert "/sem.worker1" in _seen_semaphores
        assert "/sem.worker2" in _seen_semaphores

    def test_skips_malformed_lines(self):
        """Lines with PSXSEM but too few parts or unparseable FD are skipped."""
        bad_output = """\
COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME
python  12345  shawn  bad   PSXSEM
python  abc    shawn    5u   PSXSEM              0      /sem.ok
"""
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=bad_output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            sems = get_process_semaphores()

        # "bad" line has <4 parts after the PSXSEM match triggers, but the
        # second line has "abc" as PID which raises ValueError on int()
        # Both should be skipped via except (ValueError, IndexError)
        assert len(sems) == 0


# -- cleanup_semaphores --


class TestCleanupSemaphores:
    def test_cleanup_all_semaphores(self, tmp_path):
        """cleanup_semaphores with no pattern tries to unlink all."""
        sem_file = tmp_path / "sem.test"
        sem_file.touch()

        mock_result = subprocess.CompletedProcess(
            args=["lsof"],
            returncode=0,
            stdout=f"python  12345  shawn    5u   PSXSEM              0      {sem_file}\n",
            stderr="",
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            cleanup_semaphores()

        # File was unlinked
        assert not sem_file.exists()

    def test_cleanup_with_pattern_filter(self):
        """Only semaphores matching the regex pattern are cleaned up."""
        output = """\
COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME
python  12345  shawn    5u   PSXSEM              0      /sem.worker1
python  12345  shawn    6u   PSXSEM              0      /sem.other
"""
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
            patch("pathlib.Path.unlink") as mock_unlink,
        ):
            cleanup_semaphores(pattern=r"worker")

        # Only /sem.worker1 matches the pattern
        mock_unlink.assert_called_once()

    def test_cleanup_handles_unlink_oserror(self, caplog):
        """OSError during unlink is caught and warned about."""
        caplog.set_level(logging.WARNING)
        output = "python  12345  shawn    5u   PSXSEM              0      /sem.locked\n"
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
            patch("pathlib.Path.unlink", side_effect=OSError("busy")),
        ):
            # Should not raise — logs a warning instead.
            cleanup_semaphores()

        cleanup_warnings = [
            m for m in _warning_messages(caplog) if "Failed to clean up semaphore" in m
        ]
        assert len(cleanup_warnings) == 1
        assert "busy" in cleanup_warnings[0]


# -- monitor_semaphores --


class TestMonitorSemaphores:
    def test_no_warning_below_threshold(self, caplog):
        """No warning when semaphore count is below threshold."""
        caplog.set_level(logging.WARNING)
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=LSOF_OUTPUT_WITH_SEMAPHORES, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            monitor_semaphores(threshold=60)

        threshold_warnings = [
            m
            for m in _warning_messages(caplog)
            if "High number of POSIX semaphores" in m
        ]
        assert threshold_warnings == []

    def test_warns_above_threshold(self, caplog):
        """Warning issued when semaphore count exceeds threshold."""
        caplog.set_level(logging.WARNING)
        # Create output with many semaphores
        lines = [
            f"python  12345  shawn    {i}u   PSXSEM              0      /sem.s{i}"
            for i in range(5, 70)
        ]
        output = "COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME\n"
        output += "\n".join(lines) + "\n"

        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            monitor_semaphores(threshold=10)

        threshold_warnings = [
            m
            for m in _warning_messages(caplog)
            if "High number of POSIX semaphores" in m
        ]
        assert len(threshold_warnings) == 1

    def test_groups_by_creation_point(self, caplog):
        """When above threshold, semaphores are grouped by creation point in trace log."""
        # caplog.set_level uses the integer level so loguru's TRACE (5) is captured;
        # logging.NOTSET would also work but TRACE-as-int is more explicit.
        caplog.set_level(5)
        # Pre-populate _seen_semaphores with known creation points
        _seen_semaphores["/sem.s5"].add("test.py:test_func:10")

        lines = [
            f"python  12345  shawn    {i}u   PSXSEM              0      /sem.s{i}"
            for i in range(5, 20)
        ]
        output = "COMMAND   PID   USER   FD      TYPE DEVICE SIZE/OFF NODE NAME\n"
        output += "\n".join(lines) + "\n"

        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
        ):
            monitor_semaphores(threshold=5)

        # trace_logger.trace is called for the "Semaphore list:" header plus
        # once per creation-point grouping line and FD line.
        assert len(_trace_messages(caplog)) > 0
