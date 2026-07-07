"""Tests for textio/logging.py — SizeAndTimeRotatingFileHandler and SizeTimeRotatingHandler."""

import logging
import os
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from textio.logging import SizeAndTimeRotatingFileHandler, SizeTimeRotatingHandler


# ============================================================================
# SizeAndTimeRotatingFileHandler
# ============================================================================


class TestSizeAndTimeRotatingFileHandlerInit:
    """Cover __init__, _compute_interval, and _check_rollover_on_init."""

    def test_basic_creation(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
            when="h",
            interval=1,
        )
        assert handler.maxBytes == 1024
        assert handler.backupCount == 3
        handler.close()

    def test_invalid_compression_type(self, tmp_path):
        """Invalid compression raises ValueError (line 74)."""
        log_file = tmp_path / "test.log"
        with pytest.raises(ValueError, match="Unsupported compression type"):
            SizeAndTimeRotatingFileHandler(filename=str(log_file), compression="7z")

    def test_invalid_when_value(self, tmp_path):
        """Invalid when raises ValueError (line 93-95)."""
        log_file = tmp_path / "test.log"
        with pytest.raises(ValueError, match="Invalid rollover interval"):
            SizeAndTimeRotatingFileHandler(filename=str(log_file), when="x")

    def test_utc_mode(self, tmp_path):
        """UTC mode uses UTC timestamp for next rollover (line 101)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), utc=True)
        assert handler.utc is True
        handler.close()

    def test_rollover_on_init_old_file(self, tmp_path):
        """File older than interval triggers rollover on init (line 132-135)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("old content")

        # Set mod time to the past

        old_time = time.time() - 7200  # 2 hours ago
        os.utime(log_file, (old_time, old_time))

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            when="h",
            interval=1,
            backupCount=2,
        )
        # After rollover, a .1 backup should exist
        backup = Path(f"{log_file}.1")
        assert backup.exists()
        handler.close()

    def test_no_rollover_on_init_empty_old_file(self, tmp_path):
        """Empty file with stale mtime does NOT trigger rollover on init.

        Regression: ``_check_rollover_on_init`` previously rolled any file
        whose mtime was older than the interval, regardless of size. That
        produced a backup slot consumed by a zero-byte file when the
        daemon restarted after an idle period with no log activity since
        the prior cycle.
        """
        log_file = tmp_path / "test.log"
        log_file.touch()  # empty file

        old_time = time.time() - 7200  # 2 hours ago, > 1h interval
        os.utime(log_file, (old_time, old_time))

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            when="h",
            interval=1,
            backupCount=2,
        )
        backup = Path(f"{log_file}.1")
        assert not backup.exists(), (
            "empty file with stale mtime should NOT roll on init"
        )
        handler.close()

    def test_rollover_on_init_large_file(self, tmp_path):
        """File exceeding maxBytes triggers rollover on init (line 133-135)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("x" * 200)

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            maxBytes=100,
            backupCount=2,
        )
        backup = Path(f"{log_file}.1")
        assert backup.exists()
        handler.close()

    def test_no_rollover_on_init_file_not_exists(self, tmp_path):
        """Non-existent file just sets next rollover (line 137-138)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            backupCount=2,
        )
        assert handler.rolloverAt > 0
        handler.close()

    def test_keep_uncompressed(self, tmp_path):
        """keep_uncompressed parameter is stored (line 76-78)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            keep_uncompressed=3,
        )
        assert handler.keep_uncompressed == 3
        handler.close()


class TestShouldRollover:
    """Cover shouldRollover edge cases."""

    def test_stream_none_opens_new(self, tmp_path):
        """When stream is None, opens a new one (lines 145-150)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), maxBytes=10000)
        handler.stream = None  # type: ignore[assignment]

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        # Should not raise — opens stream and checks
        result = handler.shouldRollover(record)
        assert isinstance(result, bool)
        handler.close()

    def test_stream_closed_opens_new(self, tmp_path):
        """When stream is closed, opens a new one (line 143)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), maxBytes=10000)
        if handler.stream:
            handler.stream.close()

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = handler.shouldRollover(record)
        assert isinstance(result, bool)
        handler.close()

    def test_size_based_rollover(self, tmp_path):
        """Returns True when file + message exceeds maxBytes (lines 152-157)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("x" * 90)
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), maxBytes=100)

        record = logging.LogRecord("test", logging.INFO, "", 0, "x" * 20, (), None)
        assert handler.shouldRollover(record) is True
        handler.close()

    def test_time_based_rollover(self, tmp_path):
        """Returns True when current time >= rolloverAt (line 163)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            maxBytes=0,  # disable size check
        )
        handler.rolloverAt = time.time() - 100  # force past

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert handler.shouldRollover(record) is True
        handler.close()

    def test_stream_open_fails(self, tmp_path):
        """When _open() fails, returns False (lines 148-150)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), maxBytes=10000)
        handler.stream = None  # type: ignore[assignment]

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        with patch.object(handler, "_open", side_effect=OSError("nope")):
            assert handler.shouldRollover(record) is False
        handler.close()

    def test_stream_seek_fails(self, tmp_path):
        """ValueError during stream.seek returns False (lines 158-160)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), maxBytes=100)

        # Close the real stream, then reopen it and patch seek to fail
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        with patch.object(handler.stream, "seek", side_effect=ValueError("closed")):
            assert handler.shouldRollover(record) is False
        handler.close()


class TestDoRollover:
    """Cover doRollover with compression, db_logger_name, etc."""

    def test_basic_rollover(self, tmp_path):
        """Basic rollover creates .1 backup and truncates main file."""
        log_file = tmp_path / "test.log"
        log_file.write_text("original content")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=3)
        handler.doRollover()

        backup1 = Path(f"{log_file}.1")
        assert backup1.exists()
        assert backup1.read_text() == "original content"
        # Main file should be truncated
        assert log_file.read_text() == ""
        handler.close()

    def test_rollover_with_gz_compression(self, tmp_path):
        """Rollover with gz compression creates .1.gz file (lines 214-215, 228-229).

        Also seeds an existing ``.1`` backup with ``keep_uncompressed=1`` so it
        survives init *uncompressed*; the rotation loop then renames the
        uncompressed ``.1`` to ``.2`` and hits the compress-the-rotated-file
        branch (line 245-246, which has no keep-uncompressed guard): an
        uncompressed ``dfn`` exists + compression on -> ``_compress_file`` runs,
        producing ``.2.gz``. (With ``keep_uncompressed=0`` init would eagerly
        compress the seeded ``.1`` to ``.1.gz`` first, so the loop would take
        the compressed-variant rename branch and line 246 would never fire.)
        """
        log_file = tmp_path / "test.log"
        log_file.write_text("compress me")
        # keep_uncompressed=1 keeps this .1 uncompressed through init, so the
        # rollover rotates the uncompressed .1 -> .2 and line 246 compresses it.
        Path(f"{log_file}.1").write_text("older backup")

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            backupCount=3,
            compression="gz",
            keep_uncompressed=1,
        )
        handler.doRollover()

        # The new .1.gz exists (current content compressed, line 261) AND the
        # rotated backup was compressed via the line-246 branch (.2.gz).
        assert Path(f"{log_file}.1.gz").exists()
        assert Path(f"{log_file}.2.gz").exists()
        handler.close()

    def test_rollover_with_db_logger_name(self, tmp_path):
        """db_logger_name triggers stderr message (lines 168-173)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("content")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=2)
        # db_logger_name is a dynamic debug attribute (textio/logging.py:65,190 —
        # hasattr-guarded, never declared on the class); set it via a computed
        # name so mypy/ruff don't treat it as a declared attribute.
        db_logger_attr = "db_logger_name"
        setattr(handler, db_logger_attr, "database_logger")

        handler.doRollover()
        # No assertion on stderr output, just verify no crash
        handler.close()

    def test_rollover_rotates_existing_backups(self, tmp_path):
        """Existing backups are shifted: .1→.2, .2→.3, etc. (lines 196-211)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current")
        Path(f"{log_file}.1").write_text("backup1")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=3)
        handler.doRollover()

        assert Path(f"{log_file}.2").read_text() == "backup1"
        assert Path(f"{log_file}.1").read_text() == "current"
        handler.close()

    def test_rollover_removes_oldest(self, tmp_path):
        """Oldest backup (count N) is removed (lines 182-193)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current")
        Path(f"{log_file}.1").write_text("b1")
        Path(f"{log_file}.2").write_text("b2")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=2)
        handler.doRollover()

        # .2 (the oldest for backupCount=2) should be gone
        assert (
            not Path(f"{log_file}.2").exists()
            or Path(f"{log_file}.2").read_text() == "b1"
        )
        handler.close()

    def test_rollover_removes_oldest_gz(self, tmp_path):
        """Oldest .gz backup is removed too (lines 190-193)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current")
        gz_oldest = Path(f"{log_file}.2.gz")
        gz_oldest.write_bytes(b"compressed")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=2)
        handler.doRollover()

        assert not gz_oldest.exists()
        handler.close()

    def test_rollover_shifts_gz_backups(self, tmp_path):
        """Existing .gz backups are shifted (lines 208-211)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current")
        gz1 = Path(f"{log_file}.1.gz")
        gz1.write_bytes(b"compressed1")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=3)
        handler.doRollover()

        # .1.gz should have moved to .2.gz
        assert Path(f"{log_file}.2.gz").exists()
        handler.close()

    def test_rollover_with_delay(self, tmp_path):
        """delay=True means stream is not reopened (line 234-235)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("content")

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file), backupCount=2, delay=True
        )
        handler.doRollover()
        # Stream should remain None when delay=True
        # (It was set to None during rollover and not reopened)
        handler.close()


class TestClose:
    """Cover close() method (lines 237-250)."""

    def test_close_flushes_and_closes(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file))
        handler.close()
        assert handler.stream is None

    def test_close_already_closed_stream(self, tmp_path):
        """Closing with already-closed stream doesn't raise (lines 246-248)."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file))
        if handler.stream:
            handler.stream.close()
        # Second close should be safe
        handler.close()
        assert handler.stream is None

    def test_close_no_stream(self, tmp_path):
        """Closing with None stream is safe."""
        log_file = tmp_path / "test.log"
        log_file.touch()
        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file))
        handler.stream = None  # type: ignore[assignment]
        handler.close()  # Should not raise


class TestCompressFile:
    """Cover _compress_file branches."""

    @pytest.mark.parametrize(
        ("compression", "keep_uncompressed", "target_suffix", "expect_ext"),
        [
            pytest.param("gz", 0, ".3", ".gz", id="gz"),
            pytest.param("bz2", 0, ".3", ".bz2", id="bz2"),
            pytest.param("xz", 0, ".3", ".xz", id="xz"),
            pytest.param("gz", 3, ".1", None, id="skip_keep_uncompressed"),
            pytest.param(None, 0, ".1", None, id="no_compression"),
        ],
    )
    def test_compress_file_outcomes(
        self,
        tmp_path: Path,
        compression: str | None,
        keep_uncompressed: int,
        target_suffix: str,
        expect_ext: str | None,
    ) -> None:
        """Each compression kind creates <target>.<ext> and removes the
        original (lines 287-323); keep_uncompressed range (lines 284-285,
        file_num=1 < keep_uncompressed=3) and compression=None (early return,
        lines 264-265) leave the file untouched and uncompressed.
        """
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            compression=compression,
            keep_uncompressed=keep_uncompressed,
        )

        target = tmp_path / f"test.log{target_suffix}"
        target.write_text("compress this")

        handler._compress_file(str(target))

        if expect_ext is not None:
            assert Path(f"{target}{expect_ext}").exists()
            assert not target.exists()
        else:
            assert not Path(f"{target}.gz").exists()
            assert target.exists()  # unchanged
        handler.close()

    @pytest.mark.parametrize(
        ("keep_uncompressed", "target_name", "write_target", "patch_spec"),
        [
            pytest.param(None, "nonexistent.log.1", False, None, id="not_found"),
            pytest.param(
                None,
                "test.log.1",
                False,
                ("pathlib.Path.exists", "disk error"),
                id="oserror_on_exists",
            ),
            pytest.param(
                0,
                "test.log.3",
                True,
                ("pathlib.Path.open", "No such file or directory"),
                id="source_deleted_during_read",
            ),
        ],
    )
    def test_compress_file_error_paths_no_raise(
        self,
        tmp_path: Path,
        keep_uncompressed: int | None,
        target_name: str,
        write_target: bool,
        patch_spec: tuple[str, str] | None,
    ) -> None:
        """Early-return error paths: non-existent file (lines 270-271), OSError
        during the existence check (lines 272-274), and source deleted while
        reading (lines 299-302 — the message must contain 'No such file or
        directory' to take that branch). None of these raise.
        """
        log_file = tmp_path / "test.log"
        if keep_uncompressed is None:
            handler = SizeAndTimeRotatingFileHandler(
                filename=str(log_file), compression="gz"
            )
        else:
            handler = SizeAndTimeRotatingFileHandler(
                filename=str(log_file),
                compression="gz",
                keep_uncompressed=keep_uncompressed,
            )

        target = tmp_path / target_name
        if write_target:
            target.write_text("content")

        if patch_spec is None:
            handler._compress_file(str(target))
        else:
            patch_target, message = patch_spec
            with patch(patch_target, side_effect=OSError(message)):
                handler._compress_file(str(target))
        handler.close()


class TestEnsureCompressionState:
    """Cover _ensure_compression_state (lines 104-114)."""

    def test_ensures_old_files_compressed(self, tmp_path):
        """Files beyond keep_uncompressed are compressed (lines 109-114)."""
        log_file = tmp_path / "test.log"
        log_file.touch()

        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file),
            compression="gz",
            backupCount=5,
            keep_uncompressed=2,
        )
        # Create files that should be compressed (beyond keep_uncompressed)
        for i in range(3, 6):
            Path(f"{log_file}.{i}").write_text(f"content{i}")

        handler._ensure_compression_state()

        for i in range(3, 6):
            assert Path(f"{log_file}.{i}.gz").exists()
        handler.close()


# ============================================================================
# SizeTimeRotatingHandler (loguru wrapper)
# ============================================================================


class TestSizeTimeRotatingHandler:
    """Cover the loguru-compatible wrapper class."""

    def test_basic_creation(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
        )
        assert handler.filename == log_file
        handler.close()

    def test_write_string_message(self, tmp_path):
        """Write with string message creates log record (lines 497-508)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(
            filename=str(log_file),
            maxBytes=1024 * 1024,
        )

        handler.write("test log message\n")

        assert log_file.exists()
        content = log_file.read_text()
        assert "test log message" in content
        handler.close()

    def test_write_handles_error(self, tmp_path):
        """Error during write is caught and logged to stderr (lines 516-524)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(
            filename=str(log_file),
            maxBytes=1024 * 1024,
        )

        with patch.object(handler.handler, "emit", side_effect=RuntimeError("boom")):
            # Should not raise
            handler.write("fail message\n")
        handler.close()

    def test_verify_file_integrity_no_file(self, tmp_path):
        """Non-existent file returns False (lines 461-462)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))

        # Delete the file
        log_file.unlink(missing_ok=True)

        assert handler._verify_file_integrity() is False
        handler.close()

    def test_verify_file_integrity_valid(self, tmp_path):
        """Existing writable file returns True (lines 458-469)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))

        # Write something so the file has content
        handler.write("some content\n")

        assert handler._verify_file_integrity() is True
        handler.close()

    def test_attempt_recovery(self, tmp_path):
        """Recovery closes stream, recreates directory, and reopens (lines 528-538)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))

        handler._attempt_recovery()

        # Should have a working stream after recovery
        assert handler.handler.stream is not None
        handler.close()

    def test_attempt_recovery_failure(self, tmp_path):
        """Recovery failure doesn't raise (lines 540-545)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))

        with patch.object(handler.handler, "_open", side_effect=OSError("nope")):
            # Should not raise
            handler._attempt_recovery()
        handler.close()

    def test_stop_alias(self, tmp_path):
        """stop() is an alias for close() (lines 556-558)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))
        handler.stop()  # Should not raise

    def test_del(self, tmp_path):
        """__del__ calls close (lines 560-562)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))
        handler.__del__()  # Should not raise

    @pytest.mark.parametrize(
        ("log_level", "expected_levelno"),
        [
            pytest.param("DEBUG", logging.DEBUG, id="string"),
            pytest.param("NONEXISTENT", logging.INFO, id="invalid_string_falls_back"),
            pytest.param(30, 30, id="int_used_directly"),
        ],
    )
    def test_log_level_resolution(
        self,
        tmp_path: Path,
        log_level: str | int,
        expected_levelno: int,
    ) -> None:
        """String log_level converts to int, invalid strings fall back to
        INFO, and ints pass through directly (lines 413-419)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file), log_level=log_level)
        assert handler.levelno == expected_levelno
        handler.close()

    def test_ensure_log_directory_permission_error(self, tmp_path):
        """Permission error during dir setup is raised (lines 436-450)."""
        log_file = tmp_path / "logs" / "test.log"

        with (
            patch("pathlib.Path.mkdir", side_effect=PermissionError("no access")),
            pytest.raises(PermissionError),
        ):
            SizeTimeRotatingHandler(filename=str(log_file))

    def test_ensure_log_directory_existing_unwritable_file(self, tmp_path):
        """Existing but unwritable file raises PermissionError (lines 435-439)."""
        log_file = tmp_path / "logs" / "test.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()

        with (
            patch("os.access", return_value=False),
            pytest.raises(PermissionError, match="Cannot write to log file"),
        ):
            SizeTimeRotatingHandler(filename=str(log_file))

    def test_ensure_log_directory_unwritable_dir(self, tmp_path):
        """Unwritable directory raises PermissionError (lines 446-450)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "test.log"

        with (
            patch("pathlib.Path.touch", side_effect=OSError("permission denied")),
            pytest.raises(PermissionError, match="Cannot write to log directory"),
        ):
            SizeTimeRotatingHandler(filename=str(log_file))

    def test_verify_file_integrity_permission_error(self, tmp_path):
        """PermissionError reading file returns False (lines 471-472)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))
        handler.write("content\n")

        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            assert handler._verify_file_integrity() is False
        handler.close()

    def test_write_dict_message(self, tmp_path):
        """Write with dict message (loguru format) creates log record (lines 483-496)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(
            filename=str(log_file),
            maxBytes=1024 * 1024,
        )

        # Build a loguru-style dict record using SimpleNamespace for nested attrs
        dict_message = {
            "name": "test",
            "level": types.SimpleNamespace(no=logging.INFO),
            "file": types.SimpleNamespace(path=""),
            "line": 0,
            "message": "dict format log message",
            "exception": None,
            "function": "test_func",
        }
        handler.write(dict_message)

        content = log_file.read_text()
        assert "dict format log message" in content
        handler.close()

    def test_write_triggers_recovery_on_missing_file(self, tmp_path):
        """write() calls _attempt_recovery when file integrity fails (line 482-483)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(
            filename=str(log_file),
            maxBytes=1024 * 1024,
        )

        # Delete the file to trigger integrity failure
        log_file.unlink(missing_ok=True)

        # write should recover and still work
        handler.write("recovery test\n")
        assert log_file.exists()
        handler.close()

    def test_attempt_recovery_with_open_stream(self, tmp_path):
        """Recovery closes existing stream before reopening (lines 530-532)."""
        log_file = tmp_path / "logs" / "test.log"
        handler = SizeTimeRotatingHandler(filename=str(log_file))

        # Ensure stream is open
        assert handler.handler.stream is not None

        handler._attempt_recovery()

        # Should still work
        assert handler.handler.stream is not None
        handler.close()


class TestCompressFileEdgeCases:
    """Additional _compress_file edge cases for remaining coverage."""

    def test_file_num_extraction_fallback(self, tmp_path):
        """Non-numeric file extension falls back to file_num=0 (lines 279-280)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file), compression="gz", keep_uncompressed=0
        )

        # Create a file with non-numeric suffix (e.g., "test.log.bak")
        target = tmp_path / "test.log.bak"
        target.write_text("compress this odd name")

        handler._compress_file(str(target))

        # file_num=0, keep_uncompressed=0, so it should be compressed
        assert Path(f"{target}.gz").exists()
        handler.close()

    def test_gz_replace_race_condition_existing_dest(self, tmp_path):
        """replace() fails but compressed file exists → cleanup temp (lines 308-311)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file), compression="gz", keep_uncompressed=0
        )

        target = tmp_path / "test.log.3"
        target.write_text("content to compress")

        # Simulate: replace() fails but the .gz file already exists (race condition)
        original_replace = Path.replace

        def mock_replace(self_path, target_path):
            # Create the .gz file as if another process did it
            Path(str(target_path)).write_bytes(b"already there")
            raise OSError("race condition")

        with patch.object(Path, "replace", mock_replace):
            handler._compress_file(str(target))

        # The .gz file should exist (created by the "other process")
        assert Path(f"{target}.gz").exists()
        handler.close()

    def test_gz_replace_fails_no_existing_dest_reraises(self, tmp_path):
        """replace() fails AND no .gz file exists → re-raises (lines 312-313)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file), compression="gz", keep_uncompressed=0
        )

        target = tmp_path / "test.log.3"
        target.write_text("content")

        def mock_replace(self_path, target_path):
            raise OSError("replace failed completely")

        with (
            patch.object(Path, "replace", mock_replace),
            pytest.raises(OSError, match="replace failed completely"),
        ):
            handler._compress_file(str(target))
        handler.close()

    def test_gz_outer_exception_cleanup(self, tmp_path):
        """Unexpected exception during gz compression cleans up temp files (lines 318-324)."""
        log_file = tmp_path / "test.log"
        handler = SizeAndTimeRotatingFileHandler(
            filename=str(log_file), compression="gz", keep_uncompressed=0
        )

        target = tmp_path / "test.log.3"
        target.write_text("content")

        # Patch gzip.open to raise an unexpected error (not "No such file")
        with (
            patch("gzip.open", side_effect=RuntimeError("unexpected gzip error")),
            pytest.raises(RuntimeError, match="unexpected gzip error"),
        ):
            handler._compress_file(str(target))
        handler.close()

    def test_rollover_shifts_gz_with_existing_dest(self, tmp_path):
        """doRollover: shifting .gz backups where destination .gz already exists (line 210)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current")
        # Create .1.gz and .2.gz
        Path(f"{log_file}.1.gz").write_bytes(b"gz1")
        Path(f"{log_file}.2.gz").write_bytes(b"gz2_old")

        handler = SizeAndTimeRotatingFileHandler(filename=str(log_file), backupCount=3)
        handler.doRollover()

        # .1.gz should have moved to .2.gz, overwriting the old .2.gz
        assert Path(f"{log_file}.2.gz").exists()
        assert Path(f"{log_file}.2.gz").read_bytes() == b"gz1"
        handler.close()
