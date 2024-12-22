"""Unit tests for metadata.helpers module."""

import gzip
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from metadata.helpers import SizeAndTimeRotatingFileHandler


class TestSizeAndTimeRotatingFileHandler(TestCase):
    """Test cases for SizeAndTimeRotatingFileHandler."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_filename = os.path.join(self.temp_dir, "test.log")
        self.logger = logging.getLogger("test_logger")
        self.logger.setLevel(logging.INFO)

    def tearDown(self):
        """Clean up test environment."""
        # Remove all handlers to close files
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

        # Remove test files
        for filename in os.listdir(self.temp_dir):
            os.remove(os.path.join(self.temp_dir, filename))
        os.rmdir(self.temp_dir)

    def test_size_based_rotation(self):
        """Test log rotation based on file size."""
        # Create handler with small max size
        handler = SizeAndTimeRotatingFileHandler(
            self.log_filename,
            maxBytes=100,  # Small size to trigger rotation
            backupCount=3,
        )
        self.logger.addHandler(handler)

        # Write enough data to trigger multiple rotations
        for i in range(5):
            self.logger.info("X" * 50)  # Each log should be > 50 bytes

        # Check that we have the expected number of files
        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 4)  # Original + 3 backups
        self.assertTrue(os.path.exists(f"{self.log_filename}.1"))
        self.assertTrue(os.path.exists(f"{self.log_filename}.2"))
        self.assertTrue(os.path.exists(f"{self.log_filename}.3"))

    def test_time_based_rotation(self):
        """Test log rotation based on time."""
        # Create handler with short time interval
        handler = SizeAndTimeRotatingFileHandler(
            self.log_filename,
            when="s",  # Rotate every second
            interval=1,
            backupCount=2,
        )
        self.logger.addHandler(handler)

        # Write logs with delays
        self.logger.info("First log")
        time.sleep(1.1)  # Wait for rotation
        self.logger.info("Second log")
        time.sleep(1.1)  # Wait for rotation
        self.logger.info("Third log")

        # Check that we have the expected number of files
        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 3)  # Original + 2 backups

    def test_compression_gz(self):
        """Test log compression with gzip."""
        handler = SizeAndTimeRotatingFileHandler(
            self.log_filename, maxBytes=100, backupCount=2, compression="gz"
        )
        self.logger.addHandler(handler)

        # Write enough data to trigger rotation
        self.logger.info("X" * 200)

        # Check that the rotated file is compressed
        compressed_file = f"{self.log_filename}.1.gz"
        self.assertTrue(os.path.exists(compressed_file))

        # Verify the content is readable
        with gzip.open(compressed_file, "rt") as f:
            content = f.read()
            self.assertIn("X" * 200, content)

    def test_utc_time_handling(self):
        """Test UTC time handling in rotation."""
        handler = SizeAndTimeRotatingFileHandler(
            self.log_filename, when="h", interval=1, utc=True, backupCount=1
        )
        self.logger.addHandler(handler)

        # Get current UTC time
        now = datetime.now(timezone.utc)
        # Set next rollover to 1 minute from now
        handler.rolloverAt = now.timestamp() + 60

        # Write log
        self.logger.info("Test log")

        # Simulate time passing
        time.sleep(61)

        # Write another log to trigger rotation
        self.logger.info("Another test")

        # Check that rotation occurred
        self.assertTrue(os.path.exists(f"{self.log_filename}.1"))

    def test_invalid_compression(self):
        """Test handling of invalid compression type."""
        with self.assertRaises(ValueError):
            SizeAndTimeRotatingFileHandler(self.log_filename, compression="invalid")

    def test_rollover_on_init(self):
        """Test file rollover check on initialization."""
        # Create an initial log file
        with open(self.log_filename, "w") as f:
            f.write("X" * 1000)

        # Set old modification time
        old_time = time.time() - 7200  # 2 hours ago
        os.utime(self.log_filename, (old_time, old_time))

        # Create handler with size and time thresholds
        handler = SizeAndTimeRotatingFileHandler(
            self.log_filename, maxBytes=500, when="h", interval=1, backupCount=1
        )
        self.logger.addHandler(handler)

        # Check that the file was rotated on initialization
        self.assertTrue(os.path.exists(f"{self.log_filename}.1"))

        # Original file should be empty or very small
        self.assertLess(os.path.getsize(self.log_filename), 100)

    def test_multiple_handlers(self):
        """Test multiple handlers on the same file."""
        # Create two handlers with different settings
        handler1 = SizeAndTimeRotatingFileHandler(
            self.log_filename, maxBytes=100, backupCount=1
        )
        handler2 = SizeAndTimeRotatingFileHandler(
            self.log_filename, when="s", interval=1, backupCount=1
        )
        self.logger.addHandler(handler1)
        self.logger.addHandler(handler2)

        # Write logs
        self.logger.info("X" * 150)  # Should trigger size rotation
        time.sleep(1.1)
        self.logger.info("Test")  # Should trigger time rotation

        # Check files
        files = os.listdir(self.temp_dir)
        self.assertGreaterEqual(len(files), 2)  # Should have at least original + backup
