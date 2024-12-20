import gzip
import os
import shutil
import time
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler


class SizeAndTimeRotatingFileHandler(BaseRotatingHandler):
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
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.utc = utc
        self.compression = compression  # Compression type (e.g., 'gz', '7z')
        self.interval = self._compute_interval(when, interval)
        self.rolloverAt = self._compute_next_rollover()
        self.when = when
        self._check_rollover_on_init(filename)
        super().__init__(filename, mode="a", encoding=encoding, delay=delay)

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
                current_time = time.mktime(datetime.utcnow().timetuple())

            # Check if the file exceeds the time interval
            if current_time - last_modified_time >= self.interval:
                self._perform_initial_rollover(filename)

            # Check if the file exceeds the size limit
            elif self.maxBytes > 0 and file_stat.st_size >= self.maxBytes:
                self._perform_initial_rollover(filename)
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
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() >= self.maxBytes:
                return True
        if time.time() >= self.rolloverAt:  # Time-based rollover
            return True
        return False

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # Rotate log files
        for i in range(self.backupCount - 1, 0, -1):
            sfn = f"{self.baseFilename}.{i}"
            dfn = f"{self.baseFilename}.{i + 1}"
            if os.path.exists(sfn):
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)
        dfn = f"{self.baseFilename}.1"
        if os.path.exists(self.baseFilename):
            if os.path.exists(dfn):
                os.remove(dfn)
            os.rename(self.baseFilename, dfn)

        # Compress the rolled log file if needed
        if self.compression:
            self._compress_file(dfn)

        # Compute the next rollover time
        self.rolloverAt = self._compute_next_rollover()

        if not self.delay:
            self.stream = self._open()

    def _compress_file(self, filepath):
        if self.compression == "gz":
            with open(filepath, "rb") as f_in:
                with gzip.open(f"{filepath}.gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(filepath)
        elif self.compression == "7z":
            shutil.make_archive(
                filepath,
                "7z",
                root_dir=os.path.dirname(filepath),
                base_dir=os.path.basename(filepath),
            )
            os.remove(filepath)
        elif self.compression == "lzha":
            shutil.make_archive(
                filepath,
                "zip",
                root_dir=os.path.dirname(filepath),
                base_dir=os.path.basename(filepath),
            )
            os.remove(filepath)
        else:
            raise ValueError(f"Unsupported compression type: {self.compression}")
