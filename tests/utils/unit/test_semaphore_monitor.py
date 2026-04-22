"""Tests for utils/semaphore_monitor.py — POSIX semaphore monitoring utilities."""

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

    def test_cleanup_handles_unlink_oserror(self):
        """OSError during unlink is caught and warned about."""
        output = "python  12345  shawn    5u   PSXSEM              0      /sem.locked\n"
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=output, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
            patch("pathlib.Path.unlink", side_effect=OSError("busy")),
        ):
            # Should not raise — prints a warning instead
            cleanup_semaphores()


# -- monitor_semaphores --


class TestMonitorSemaphores:
    def test_no_warning_below_threshold(self):
        """No warning when semaphore count is below threshold."""
        mock_result = subprocess.CompletedProcess(
            args=["lsof"], returncode=0, stdout=LSOF_OUTPUT_WITH_SEMAPHORES, stderr=""
        )
        with (
            patch("utils.semaphore_monitor.shutil.which", return_value="/usr/bin/lsof"),
            patch("utils.semaphore_monitor.subprocess.run", return_value=mock_result),
            patch("utils.semaphore_monitor.print_warning") as mock_warn,
        ):
            monitor_semaphores(threshold=60)

        mock_warn.assert_not_called()

    def test_warns_above_threshold(self):
        """Warning issued when semaphore count exceeds threshold."""
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
            patch("utils.semaphore_monitor.print_warning") as mock_warn,
        ):
            monitor_semaphores(threshold=10)

        mock_warn.assert_called_once()
        assert "High number of POSIX semaphores" in mock_warn.call_args[0][0]

    def test_groups_by_creation_point(self):
        """When above threshold, semaphores are grouped by creation point in trace log."""
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
            patch("utils.semaphore_monitor.print_warning"),
            patch("utils.semaphore_monitor.trace_logger") as mock_trace,
        ):
            monitor_semaphores(threshold=5)

        # trace_logger.trace should be called multiple times for grouping
        assert mock_trace.trace.call_count > 0
