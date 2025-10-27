"""Unit tests for helpers.logging module."""

import gzip
import logging
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from textio.logging import SizeAndTimeRotatingFileHandler


@pytest.fixture
def log_setup():
    """Set up test environment."""
    temp_dir = Path(tempfile.mkdtemp())
    log_filename = str(temp_dir / "test.log")
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)

    yield str(temp_dir), log_filename, logger

    # Cleanup
    handler_list = (
        logger.handlers.copy()
    )  # Make a copy to avoid modification during iteration
    for handler in handler_list:
        logger.removeHandler(handler)
        handler.close()

    # Remove test files
    try:
        for file_path in temp_dir.iterdir():
            file_path.unlink()
        temp_dir.rmdir()
    except OSError as e:
        print(f"Warning: Cleanup issue: {e}")


def test_size_based_rotation(log_setup):
    """Test log rotation based on file size."""
    temp_dir, log_filename, logger = log_setup

    # Create handler with small max size
    handler = SizeAndTimeRotatingFileHandler(
        log_filename,
        maxBytes=100,  # Small size to trigger rotation
        backupCount=3,
    )
    logger.addHandler(handler)

    # Write enough data to trigger multiple rotations
    for i in range(5):
        logger.info("X" * 50)  # Each log should be > 50 bytes

    # Check that we have the expected number of files
    files = list(Path(temp_dir).iterdir())
    assert len(files) == 4  # Original + 3 backups
    assert Path(f"{log_filename}.1").exists()
    assert Path(f"{log_filename}.2").exists()
    assert Path(f"{log_filename}.3").exists()


def test_time_based_rotation(log_setup):
    """Test log rotation based on time."""
    temp_dir, log_filename, logger = log_setup

    # Create handler with short time interval
    handler = SizeAndTimeRotatingFileHandler(
        log_filename,
        when="s",  # Rotate every second
        interval=1,
        backupCount=2,
    )
    logger.addHandler(handler)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Set the initial rollover time
        handler.rolloverAt = (now + timedelta(seconds=1)).timestamp()

        # Write the first log
        logger.info("First log")

        # Simulate the passage of time to trigger the first rollover
        mock_datetime.now.return_value = now + timedelta(seconds=1.1)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Second log")

        # Simulate the passage of time to trigger the second rollover
        mock_datetime.now.return_value = now + timedelta(seconds=2.2)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Third log")

    # Check that we have the expected number of files
    files = list(Path(temp_dir).iterdir())
    assert len(files) == 3  # Original + 2 backups


def test_compression_gz(log_setup):
    """Test log compression with gzip."""
    temp_dir, log_filename, logger = log_setup

    # Create handler with compression
    handler = SizeAndTimeRotatingFileHandler(
        log_filename, maxBytes=100, backupCount=2, compression="gz"
    )
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Write enough data to trigger rotation
    logger.info("X" * 200)
    handler.flush()

    # Write more data to trigger another rotation
    logger.info("Y" * 100)
    handler.flush()

    # Check that the rotated file is compressed
    compressed_file = f"{log_filename}.1.gz"
    assert Path(compressed_file).exists()

    # Verify the content is readable
    with gzip.open(compressed_file, "rt") as f:
        content = f.read()
        assert ("X" * 200) in content


def test_utc_time_handling(log_setup):
    """Test UTC time handling in rotation."""
    temp_dir, log_filename, logger = log_setup

    handler = SizeAndTimeRotatingFileHandler(
        log_filename, when="h", interval=1, utc=True, backupCount=1
    )
    logger.addHandler(handler)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current UTC time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        # Set initial rollover time to 1 minute from now
        handler.rolloverAt = (now + timedelta(minutes=1)).timestamp()

        # Write the first log
        logger.info("Test log")

        # Simulate the passage of time to trigger rotation
        mock_datetime.now.return_value = now + timedelta(minutes=1, seconds=1)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Another test")

    # Check that rotation occurred
    assert Path(f"{log_filename}.1").exists()


def test_invalid_compression(log_setup):
    """Test handling of invalid compression type."""
    temp_dir, log_filename, logger = log_setup

    with pytest.raises(ValueError, match="invalid"):
        SizeAndTimeRotatingFileHandler(log_filename, compression="invalid")


def test_rollover_on_init(log_setup):
    """Test file rollover check on initialization."""
    temp_dir, log_filename, logger = log_setup

    # Create an initial log file
    with Path(log_filename).open("w") as f:
        f.write("X" * 1000)

    # Set old modification time
    old_time = time.time() - 7200  # 2 hours ago
    os.utime(log_filename, (old_time, old_time))

    # Create handler with size and time thresholds
    handler = SizeAndTimeRotatingFileHandler(
        log_filename, maxBytes=500, when="h", interval=1, backupCount=1
    )
    logger.addHandler(handler)

    # Check that the file was rotated on initialization
    assert Path(f"{log_filename}.1").exists()

    # Original file should be empty or very small
    assert Path(log_filename).stat().st_size < 100


def test_multiple_handlers(log_setup):
    """Test multiple handlers on the same file."""
    temp_dir, log_filename, logger = log_setup

    # Create two handlers with different settings
    handler1 = SizeAndTimeRotatingFileHandler(log_filename, maxBytes=100, backupCount=1)
    handler2 = SizeAndTimeRotatingFileHandler(
        log_filename, when="s", interval=1, backupCount=1
    )
    logger.addHandler(handler1)
    logger.addHandler(handler2)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Write logs with simulated time and size triggers
        logger.info("X" * 150)  # Should trigger size rotation
        mock_datetime.now.return_value = now + timedelta(seconds=1.1)
        logger.info("Test")  # Should trigger time rotation

    # Check files
    files = list(Path(temp_dir).iterdir())
    assert len(files) >= 2  # Should have at least original + backup
