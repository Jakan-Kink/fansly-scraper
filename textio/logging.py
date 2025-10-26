"""Logging Module

This module provides custom logging handlers for advanced log management.
The main handler class SizeTimeRotatingHandler supports:
- Combined size and time-based log rotation
- Multiple compression formats (gz, 7z, lzha)
- UTC time support
- Configurable backup count and intervals
- Proper cleanup and compression

Note: All logger configuration is now centralized in config/logging.py.
This module only provides the handler implementation.
"""

import contextlib
import gzip
import logging
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Any


class SizeAndTimeRotatingFileHandler(BaseRotatingHandler):
    """A logging handler that rotates files based on both size and time.

    This handler extends BaseRotatingHandler to provide combined size and time-based
    rotation with compression support. It can rotate logs when either the file size
    exceeds a threshold or when a time interval has passed.

    Attributes:
        maxBytes: Maximum file size in bytes before rotation
        backupCount: Number of backup files to keep
        utc: Whether to use UTC time for rotation calculations
        compression: Compression format to use ('gz', '7z', 'lzha')
        interval: Time interval between rotations in seconds
        rolloverAt: Timestamp for next scheduled rotation
        when: Time unit for rotation ('s', 'm', 'h', 'd', 'w')
        keep_uncompressed: Number of most recent backup files to keep uncompressed

    Note:
        - Rotation occurs if either size or time threshold is exceeded
        - Compressed files are automatically cleaned up
        - UTC time support for consistent rotation across timezones
        - Files are kept uncompressed based on their recency (e.g., keep_uncompressed=2
          keeps log.1 and log.2 uncompressed while compressing older files)
        - db_logger_name may be set dynamically for debugging purposes
    """

    def __init__(
        self,
        filename,
        maxBytes=0,
        backupCount=5,
        when="h",
        interval=1,
        utc=False,
        compression=None,
        encoding=None,
        delay=False,
        keep_uncompressed=0,
    ):
        super().__init__(filename, mode="a", encoding=encoding, delay=delay)
        # Set terminator to empty string to prevent automatic newlines
        self.terminator = ""
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.utc = utc
        if compression and compression not in ["gz", "7z", "lzha"]:
            raise ValueError(f"Unsupported compression type: {compression}")
        self.compression = compression
        self.keep_uncompressed = (
            keep_uncompressed  # Number of uncompressed files to keep
        )
        self.interval = self._compute_interval(when, interval)
        self.rolloverAt = self._compute_next_rollover()
        self.when = when
        self._check_rollover_on_init(filename)

    def _compute_interval(self, when, interval):
        intervals = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 604800,
        }
        if when not in intervals:
            raise ValueError(
                f"Invalid rollover interval '{when}'. Use 's', 'm', 'h', 'd', or 'w'."
            )
        return interval * intervals[when]

    def _compute_next_rollover(self):
        current_time = time.time()
        if self.utc:
            current_time = datetime.now(UTC).timestamp()
        return current_time + self.interval

    def _ensure_compression_state(self):
        """Ensure all files that should be compressed are compressed."""
        if not self.compression:
            return

        for i in range(self.keep_uncompressed + 1, self.backupCount + 1):
            filename = f"{self.baseFilename}.{i}"
            # Ignore errors when checking file existence or compressing
            with contextlib.suppress(OSError):
                if Path(filename).exists():
                    self._compress_file(filename)

    def _check_rollover_on_init(self, filename):
        """
        Check the modification date and size of the file on initialization
        and perform a rollover if needed.
        """
        # Ensure files are in correct compression state
        self._ensure_compression_state()
        filepath = Path(filename)
        if filepath.exists():
            file_stat = filepath.stat()
            last_modified_time = file_stat.st_mtime
            current_time = time.time()
            if self.utc:
                current_time = datetime.now(UTC).timestamp()

            # Check if the file exceeds the time interval
            if current_time - last_modified_time >= self.interval or (
                self.maxBytes > 0 and file_stat.st_size >= self.maxBytes
            ):
                self.doRollover()
        else:
            # If the file doesn't exist, set the next rollover time
            self.rolloverAt = self._compute_next_rollover()

    def shouldRollover(self, record):  # noqa: N802 - Override BaseRotatingHandler method
        # Defensive check: stream can be None with delay=True or closed by another thread
        if self.stream is None or (
            hasattr(self.stream, "closed") and self.stream.closed
        ):
            try:
                self.stream = self._open()
            except Exception:
                # If we can't open the stream, assume no rollover needed
                # This prevents crashes during concurrent shutdown
                return False

        if self.maxBytes > 0:  # Size-based rollover
            msg = self.format(record)
            try:
                self.stream.seek(0, 2)  # seek to end of file
                if self.stream.tell() + len(msg) + 1 >= self.maxBytes:
                    return True
            except (ValueError, OSError):
                # Stream was closed between check and use - don't rollover
                return False

        # Time-based rollover
        return time.time() >= self.rolloverAt

    def doRollover(self):  # noqa: N802 - Override BaseRotatingHandler method
        # Add a special message to help with debugging database connections during rotation
        # This will appear in the log file right before rotation happens
        if hasattr(self, "db_logger_name") and self.db_logger_name:
            with contextlib.suppress(ValueError, OSError):
                print(
                    f"About to rotate log file for {self.db_logger_name} - watch database connection",
                    file=sys.stderr,
                )
        if self.stream:
            try:
                self.stream.flush()
                self.stream.close()
            finally:
                self.stream = None  # type: ignore[assignment]

        # Remove oldest backup if it exists
        if self.backupCount > 0:
            oldest = f"{self.baseFilename}.{self.backupCount}"
            oldest_path = Path(oldest)
            oldest_gz = Path(f"{oldest}.gz")
            # Ignore errors when removing old backup files
            with contextlib.suppress(OSError):
                if oldest_path.exists():
                    oldest_path.unlink()
            # Ignore errors when removing old compressed backup files
            with contextlib.suppress(OSError):
                if oldest_gz.exists():
                    oldest_gz.unlink()

        # Rotate log files
        for i in range(self.backupCount - 1, 0, -1):
            sfn = f"{self.baseFilename}.{i}"
            dfn = f"{self.baseFilename}.{i + 1}"
            sfn_path = Path(sfn)
            dfn_path = Path(dfn)
            sfn_gz = Path(f"{sfn}.gz")
            dfn_gz = Path(f"{dfn}.gz")

            if sfn_path.exists():
                if dfn_path.exists():
                    dfn_path.unlink()  # pragma: no cover - defensive check, destination cleared in cleanup
                sfn_path.rename(dfn_path)
            elif sfn_gz.exists():
                if dfn_gz.exists():
                    dfn_gz.unlink()
                sfn_gz.rename(dfn_gz)

            # Check if the rotated file should be compressed
            if dfn_path.exists() and self.compression:
                self._compress_file(dfn)

        dfn = f"{self.baseFilename}.1"
        dfn_path = Path(dfn)
        base_path = Path(self.baseFilename)
        if base_path.exists():
            if dfn_path.exists():
                dfn_path.unlink()  # pragma: no cover - defensive check, .1 cleared in cleanup or moved in rotation
            shutil.copy2(self.baseFilename, dfn)
            with open(self.baseFilename, "w") as f:
                f.truncate(0)

            # Compress the new rotated file if needed
            if self.compression:
                self._compress_file(dfn)

        # Compute the next rollover time
        self.rolloverAt = self._compute_next_rollover()

        if not self.delay:
            self.stream = self._open()

    def close(self):
        """
        Closes the stream and ensures proper cleanup.
        Idempotent - safe to call multiple times.
        """
        if self.stream:
            # Ignore "I/O operation on closed file" and other close errors
            # This can happen if the file was deleted or stream already closed
            with contextlib.suppress(ValueError, OSError, AttributeError):
                # Check if stream is already closed before attempting operations
                if not (hasattr(self.stream, "closed") and self.stream.closed):
                    self.stream.flush()
                    self.stream.close()
            self.stream = None  # type: ignore[assignment]

    def _compress_file(self, filepath):
        """Compress a log file with proper error handling.

        Args:
            filepath: Path to the file to compress

        This method:
        1. Checks if compression is enabled
        2. Verifies file exists and should be compressed
        3. Handles race conditions during compression
        4. Cleans up partial files on error
        """
        if not self.compression:
            return

        # Try atomic operations to check file existence
        filepath_path = Path(filepath)
        try:
            if not filepath_path.exists():
                return
        except OSError:
            # File might have been deleted between check and use
            return

        # Extract the file number from the filepath (e.g., "log.1" -> 1)
        try:
            file_num = int(filepath_path.name.split(".")[-1])
        except (ValueError, IndexError):
            file_num = 0

        # Skip compression if this file should be kept uncompressed
        # keep_uncompressed=3 means keep files 1 and 2 uncompressed (file numbers < 3)
        if self.keep_uncompressed > 0 and file_num < self.keep_uncompressed:
            return

        if self.compression == "gz":
            compressed_path = f"{filepath}.gz"
            temp_path = f"{compressed_path}.tmp"

            try:
                # First compress to a temporary file
                try:
                    with (
                        filepath_path.open("rb") as f_in,
                        gzip.open(temp_path, "wb") as f_out,
                    ):
                        shutil.copyfileobj(f_in, f_out)
                except OSError as e:
                    if "No such file or directory" in str(e):
                        # File was deleted while we were reading it
                        return
                    raise

                # Then atomically move the temp file to final location
                try:
                    Path(temp_path).replace(compressed_path)
                except OSError:
                    # Another process might have created the file
                    if Path(compressed_path).exists():
                        Path(temp_path).unlink(missing_ok=True)
                    else:
                        raise

                # Finally try to remove the original
                with contextlib.suppress(OSError):
                    filepath_path.unlink(missing_ok=True)
            except Exception:
                # Clean up any partial files
                for path in [temp_path, compressed_path]:
                    with contextlib.suppress(OSError):
                        Path(path).unlink(missing_ok=True)
                # Always re-raise the exception after cleanup
                raise
        elif self.compression == "7z":
            try:
                shutil.make_archive(
                    filepath,
                    "7z",
                    root_dir=filepath_path.parent,
                    base_dir=filepath_path.name,
                )
                filepath_path.unlink()
            except Exception:
                archive_path = Path(f"{filepath}.7z")
                if archive_path.exists():
                    archive_path.unlink()
                raise
        elif self.compression == "lzha":
            try:
                shutil.make_archive(
                    filepath,
                    "zip",
                    root_dir=filepath_path.parent,
                    base_dir=filepath_path.name,
                )
                filepath_path.unlink()
            except Exception:
                archive_path = Path(f"{filepath}.zip")
                if archive_path.exists():
                    archive_path.unlink()
                raise
        else:
            # This should never happen due to validation in __init__, but be defensive
            raise ValueError(f"Unsupported compression type: {self.compression}")


class SizeTimeRotatingHandler:
    """A loguru-compatible handler that uses SizeAndTimeRotatingFileHandler.

    This handler provides both size and time-based rotation with the ability
    to keep N most recent files uncompressed while compressing older files.
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = 0,
        backupCount: int = 5,
        when: str = "h",
        interval: int = 1,
        utc: bool = False,
        compression: str = "gz",
        keep_uncompressed: int = 0,
        encoding: str = "utf-8",
        log_level: str | int = "INFO",
    ):
        """Initialize the handler.

        Args:
            filename: Base name of the log file
            maxBytes: Max size of each log file before rotation
            backupCount: Total number of backup files to keep
            when: Rotation interval unit ('s', 'm', 'h', 'd', 'w')
            interval: Number of units between rotations
            utc: Use UTC time for rotation timing
            compression: Compression format ('gz', '7z', 'lzha')
            keep_uncompressed: Number of recent files to keep uncompressed
            encoding: File encoding
            log_level: Logging level (default: INFO)
        """
        # Validate and prepare log file path
        self.filename = Path(filename)
        self._ensure_log_directory()

        self.handler = SizeAndTimeRotatingFileHandler(
            filename=str(self.filename),
            maxBytes=maxBytes,
            backupCount=backupCount,
            when=when,
            interval=interval,
            utc=utc,
            compression=compression,
            keep_uncompressed=keep_uncompressed,
            encoding=encoding,
        )
        self.formatter = logging.Formatter(
            fmt="%(message)s",
            datefmt=None,
        )
        self.handler.setFormatter(self.formatter)
        # Convert level to int if it's a string (for backward compatibility)
        if isinstance(log_level, str):
            try:
                self.levelno = getattr(logging, log_level.upper())
            except AttributeError:
                self.levelno = logging.INFO
        else:
            self.levelno = log_level

    def _ensure_log_directory(self) -> None:
        """Ensure the log directory exists and is writable."""
        try:
            # Create directory if it doesn't exist
            self.filename.parent.mkdir(parents=True, exist_ok=True)

            # Test write permissions
            if self.filename.exists():
                if not os.access(self.filename, os.W_OK):
                    raise PermissionError(f"Cannot write to log file: {self.filename}")
            else:
                # Test directory write permissions by creating a temp file
                test_file = self.filename.parent / f".{self.filename.name}.test"
                try:
                    test_file.touch()
                    test_file.unlink()
                except OSError as e:
                    raise PermissionError(
                        f"Cannot write to log directory: {self.filename.parent}"
                    ) from e

        except Exception as e:
            # Avoid printing to stderr during cleanup - pytest may have closed it
            with contextlib.suppress(ValueError, OSError):
                print(f"Log directory setup failed: {e}", file=sys.stderr)
            raise

    def _verify_file_integrity(self) -> bool:
        """Verify that the log file exists and is accessible."""
        try:
            if not self.filename.exists():
                return False

            # Try to read the file
            with open(self.filename, encoding="utf-8") as f:
                f.read(1)  # Try to read just one character

            # Check write permissions
            return os.access(self.filename, os.W_OK)

        except (OSError, PermissionError):
            return False

    def write(self, message: str | dict[str, Any]) -> None:
        """Write a log record with file verification and recovery.

        Args:
            message: The log record as a string or dict from loguru
        """
        try:
            # Verify file integrity before writing
            if not self._verify_file_integrity():
                self._attempt_recovery()

            if isinstance(message, dict):
                # Handle dict format from loguru
                record = logging.LogRecord(
                    name=message["name"],
                    level=message["level"].no,
                    pathname=message["file"].path,
                    lineno=message["line"],
                    msg=message["message"],
                    args=(),
                    exc_info=message["exception"],
                    func=message["function"],
                )
            else:
                # Handle string format
                record = logging.LogRecord(
                    name=__name__,
                    level=self.levelno,
                    pathname="",
                    lineno=0,
                    msg=str(message),
                    args=(),
                    exc_info=None,
                    func=None,
                )

            # Write record and flush to ensure data is written promptly
            self.handler.emit(record)
            if self.handler.stream:
                with contextlib.suppress(Exception):
                    self.handler.stream.flush()

        except Exception as e:
            # Avoid printing to stderr during cleanup - pytest may have closed it
            with contextlib.suppress(ValueError, OSError):
                print(f"Error in SizeTimeRotatingHandler: {e}", file=sys.stderr)
            # On error, try to close and reset stream for recovery
            with contextlib.suppress(Exception):
                if self.handler.stream:
                    self.handler.stream.close()
                    self.handler.stream = None  # type: ignore[assignment]

    def _attempt_recovery(self) -> None:
        """Attempt to recover from file access issues."""
        try:
            # Close existing stream if open
            if self.handler.stream:
                self.handler.stream.close()
                self.handler.stream = None  # type: ignore[assignment]

            # Re-ensure directory structure
            self._ensure_log_directory()

            # Try to recreate the handler's stream
            self.handler.stream = self.handler._open()

        except Exception as e:
            # Avoid printing to stderr during cleanup - pytest may have closed it
            # stderr is closed, can't report error
            with contextlib.suppress(ValueError, OSError):
                print(f"Log recovery failed: {e}", file=sys.stderr)
                print("Falling back to stderr for logging", file=sys.stderr)

    def close(self) -> None:
        """Close the handler and all file handles.

        Idempotent - safe to call multiple times.
        Avoids printing to stderr during cleanup to prevent pytest issues.
        """
        with contextlib.suppress(Exception):
            self.handler.close()

    def stop(self) -> None:
        """Close the handler (alias for close)."""
        self.close()

    def __del__(self) -> None:
        """Ensure handler is closed when object is deleted."""
        self.close()
