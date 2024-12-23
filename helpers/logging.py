"""Helper utilities for metadata handling.

This module provides utility classes and functions for metadata operations,
particularly focusing on log file management and rotation. It includes advanced
logging handlers that support both size and time-based rotation with compression.

Features:
- Combined size and time-based log rotation
- Multiple compression formats (gz, 7z, lzha)
- UTC time support
- Configurable backup count and intervals
"""

import gzip
import os
import shutil
import time
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler


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

    Note:
        - Rotation occurs if either size or time threshold is exceeded
        - Compressed files are automatically cleaned up
        - UTC time support for consistent rotation across timezones
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
    ):
        super().__init__(filename, mode="a", encoding=encoding, delay=delay)
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.utc = utc
        if compression and compression not in ["gz", "7z", "lzha"]:
            raise ValueError(f"Unsupported compression type: {compression}")
        self.compression = compression  # Compression type (e.g., 'gz', '7z')
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

    def _check_rollover_on_init(self, filename):
        """
        Check the modification date and size of the file on initialization
        and perform a rollover if needed.
        """
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
            self.stream.flush()
            self.stream.close()
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

        dfn = f"{self.baseFilename}.1"
        if os.path.exists(self.baseFilename):
            if os.path.exists(dfn):
                os.remove(dfn)
            shutil.copy2(self.baseFilename, dfn)
            os.truncate(self.baseFilename, 0)

            # Compress the rolled log file if needed
            if self.compression:
                self._compress_file(dfn)

        # Compute the next rollover time
        self.rolloverAt = self._compute_next_rollover()

        if not self.delay:
            self.stream = self._open()

    def _compress_file(self, filepath):
        if not self.compression:
            return

        if not os.path.exists(filepath):
            return

        if self.compression == "gz":
            compressed_path = f"{filepath}.gz"
            try:
                with open(filepath, "rb") as f_in:
                    with gzip.open(compressed_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(filepath)
            except Exception as e:
                if os.path.exists(compressed_path):
                    os.remove(compressed_path)
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
