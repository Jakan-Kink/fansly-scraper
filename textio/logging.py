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

import gzip
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
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
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.utc = utc
        if compression and compression not in ["gz", "7z", "lzha"]:
            raise ValueError(f"Unsupported compression type: {compression}")
        self.compression = compression  # Compression type (e.g., 'gz', '7z')
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
            current_time = datetime.now(timezone.utc).timestamp()
        return current_time + self.interval

    def _ensure_compression_state(self):
        """Ensure all files that should be compressed are compressed."""
        if not self.compression:
            return

        for i in range(self.keep_uncompressed + 1, self.backupCount + 1):
            filename = f"{self.baseFilename}.{i}"
            if os.path.exists(filename):
                self._compress_file(filename)

    def _check_rollover_on_init(self, filename):
        """
        Check the modification date and size of the file on initialization
        and perform a rollover if needed.
        """
        # Ensure files are in correct compression state
        self._ensure_compression_state()
        if os.path.exists(filename):
            file_stat = os.stat(filename)
            last_modified_time = file_stat.st_mtime
            current_time = time.time()
            if self.utc:
                current_time = datetime.now(timezone.utc).timestamp()

            # Check if the file exceeds the time interval
            if current_time - last_modified_time >= self.interval:
                self.doRollover()

            # Check if the file exceeds the size limit
            elif self.maxBytes > 0 and file_stat.st_size >= self.maxBytes:
                self.doRollover()
        else:
            # If the file doesn't exist, set the next rollover time
            self.rolloverAt = self._compute_next_rollover()

    def _perform_initial_rollover(self, filename):
        """
        Perform a rollover before the handler starts if required by size or time.
        """
        for i in range(self.backupCount - 1, 0, -1):
            sfn = f"{filename}.{i}"
            dfn = f"{filename}.{i + 1}"
            if os.path.exists(sfn):
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)
        dfn = f"{filename}.1"
        if os.path.exists(filename):
            if os.path.exists(dfn):
                os.remove(dfn)
            os.rename(filename, dfn)

        # Compress the rolled log file if needed
        if self.compression:
            self._compress_file(dfn)

        # Compute the next rollover time
        self.rolloverAt = self._compute_next_rollover()

    def shouldRollover(self, record):
        if self.stream is None:  # Open the stream if not already open
            self.stream = self._open()
        if self.maxBytes > 0:  # Size-based rollover
            msg = self.format(record)
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(msg) + 1 >= self.maxBytes:
                return True
        if time.time() >= self.rolloverAt:  # Time-based rollover
            return True
        return False

    def doRollover(self):
        if self.stream:
            try:
                self.stream.flush()
                self.stream.close()
            finally:
                self.stream = None

        # Remove oldest backup if it exists
        if self.backupCount > 0:
            oldest = f"{self.baseFilename}.{self.backupCount}"
            if os.path.exists(oldest):
                os.remove(oldest)
            if os.path.exists(f"{oldest}.gz"):
                os.remove(f"{oldest}.gz")

        # Rotate log files
        for i in range(self.backupCount - 1, 0, -1):
            sfn = f"{self.baseFilename}.{i}"
            dfn = f"{self.baseFilename}.{i + 1}"
            if os.path.exists(sfn):
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)
            elif os.path.exists(f"{sfn}.gz"):
                if os.path.exists(f"{dfn}.gz"):
                    os.remove(f"{dfn}.gz")
                os.rename(f"{sfn}.gz", f"{dfn}.gz")

            # Check if the rotated file should be compressed
            if os.path.exists(dfn) and self.compression:
                self._compress_file(dfn)

        dfn = f"{self.baseFilename}.1"
        if os.path.exists(self.baseFilename):
            if os.path.exists(dfn):
                os.remove(dfn)
            shutil.copy2(self.baseFilename, dfn)
            os.truncate(self.baseFilename, 0)

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
        """
        if self.stream:
            try:
                self.stream.flush()
                self.stream.close()
            finally:
                self.stream = None

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

        # Use atomic operations to check file existence
        try:
            if not os.path.exists(filepath):
                return
        except OSError:
            # File might have been deleted between check and use
            return

        # Extract the file number from the filepath (e.g., "log.1" -> 1)
        try:
            file_num = int(filepath.split(".")[-1])
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
                    with open(filepath, "rb") as f_in:
                        with gzip.open(temp_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                except OSError as e:
                    if "No such file or directory" in str(e):
                        # File was deleted while we were reading it
                        return
                    raise

                # Then atomically move the temp file to final location
                try:
                    os.replace(temp_path, compressed_path)
                except OSError:
                    # Another process might have created the file
                    if os.path.exists(compressed_path):
                        os.remove(temp_path)
                    else:
                        raise

                # Finally try to remove the original
                try:
                    os.remove(filepath)
                except OSError:
                    # File might already be gone
                    pass

            except Exception as e:
                # Clean up any partial files
                for path in [temp_path, compressed_path]:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        pass
                # Only re-raise if original file still exists
                if os.path.exists(filepath):
                    raise e
        elif self.compression == "7z":
            try:
                shutil.make_archive(
                    filepath,
                    "7z",
                    root_dir=os.path.dirname(filepath),
                    base_dir=os.path.basename(filepath),
                )
                os.remove(filepath)
            except Exception as e:
                if os.path.exists(f"{filepath}.7z"):
                    os.remove(f"{filepath}.7z")
                raise e
        elif self.compression == "lzha":
            try:
                shutil.make_archive(
                    filepath,
                    "zip",
                    root_dir=os.path.dirname(filepath),
                    base_dir=os.path.basename(filepath),
                )
                os.remove(filepath)
            except Exception as e:
                if os.path.exists(f"{filepath}.zip"):
                    os.remove(f"{filepath}.zip")
                raise e
        else:
            raise ValueError(f"Unsupported compression type: {self.compression}")


class SizeTimeRotatingHandler:
    """A loguru-compatible handler that uses SizeAndTimeRotatingFileHandler.

    This handler provides both size and time-based rotation with the ability
    to keep N most recent files uncompressed while compressing older files.
    """

    def __init__(
        self,
        filename: str,
        max_bytes: int = 0,
        backup_count: int = 5,
        when: str = "h",
        interval: int = 1,
        utc: bool = False,
        compression: str = "gz",
        keep_uncompressed: int = 0,
        encoding: str = "utf-8",
        log_level: str = "INFO",
    ):
        """Initialize the handler.

        Args:
            filename: Base name of the log file
            max_bytes: Max size of each log file before rotation
            backup_count: Total number of backup files to keep
            when: Rotation interval unit ('s', 'm', 'h', 'd', 'w')
            interval: Number of units between rotations
            utc: Use UTC time for rotation timing
            compression: Compression format ('gz', '7z', 'lzha')
            keep_uncompressed: Number of recent files to keep uncompressed
            encoding: File encoding
            log_level: Logging level (default: INFO)
        """
        self.handler = SizeAndTimeRotatingFileHandler(
            filename=filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
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
                self.levelno = int(log_level)
            except ValueError:
                self.levelno = logging.INFO
        else:
            self.levelno = log_level

    def write(self, message: str | dict[str, Any]) -> None:
        """Write a log record.

        Args:
            message: The log record as a string or dict from loguru
        """
        try:
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

            # Write record with proper cleanup
            try:
                self.handler.emit(record)
            finally:
                # Always ensure stream is closed
                if self.handler.stream:
                    try:
                        self.handler.stream.flush()
                    except Exception:
                        pass
                    try:
                        self.handler.stream.close()
                    except Exception:
                        pass
                    self.handler.stream = None
        except Exception as e:
            print(f"Error in SizeTimeRotatingHandler: {e}", file=sys.stderr)
            # Ensure stream is flushed even on error
            try:
                if self.handler.stream:
                    self.handler.stream.flush()
            except Exception:
                pass

    def close(self) -> None:
        """Close the handler and all file handles."""
        try:
            if self.handler.stream:
                self.handler.stream.flush()
                self.handler.stream.close()
                self.handler.stream = None
            self.handler.close()
        except Exception as e:
            print(f"Error closing handler: {e}", file=sys.stderr)

    def stop(self) -> None:
        """Close the handler (alias for close)."""
        self.close()

    def __del__(self) -> None:
        """Ensure handler is closed when object is deleted."""
        self.close()
