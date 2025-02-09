"""Logging configuration for Stash."""

import logging
import sys
from pathlib import Path
from pprint import pformat

from textio.logging import SizeAndTimeRotatingFileHandler

# Logging setup
logs_dir = Path.cwd() / "logs"
logs_dir.mkdir(exist_ok=True)
log_file = logs_dir / "stash.log"

# Root logger for all stash components
logger = logging.getLogger("fansly.stash")
logger.handlers.clear()
logger.setLevel(logging.DEBUG)
logger.propagate = False

# File handler with rotation
file_handler = SizeAndTimeRotatingFileHandler(
    filename=str(log_file),
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    when="h",  # Hourly rotation
    interval=1,
    utc=True,
    compression="gz",
    keep_uncompressed=2,  # Keep 2 most recent logs uncompressed
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# # Console handler
# console_handler = logging.StreamHandler(sys.stdout)
# console_handler.setLevel(logging.DEBUG)  # Show debug messages in console
# console_formatter = logging.Formatter("%(levelname)s: %(message)s")
# console_handler.setFormatter(console_formatter)
# logger.addHandler(console_handler)


def debug_print(obj, logger_name: str | None = None):
    """Debug printing with proper formatting.

    Args:
        obj: Object to format and log
        logger_name: Optional logger name to use (e.g., "processing", "client")
                    If None, uses root stash logger
    """
    try:
        formatted = pformat(obj, indent=2)
        if logger_name:
            log = logging.getLogger(f"fansly.stash.{logger_name}")
        else:
            log = logger
        log.debug(formatted)
        for handler in log.handlers:
            handler.flush()
    except Exception as e:
        print(f"Failed to log debug message: {e}", file=sys.stderr)


# Component loggers
client_logger = logging.getLogger("fansly.stash.client")
processing_logger = logging.getLogger("fansly.stash.processing")

# These will inherit handlers from the root logger
client_logger.propagate = True
processing_logger.propagate = True
