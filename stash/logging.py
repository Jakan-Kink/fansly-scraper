"""Stash logging utilities.

This module provides specialized loggers for Stash operations:
- client_logger - For Stash client operations
- processing_logger - For Stash data processing

Note: All logger configuration is now centralized in config/logging.py.
This module only provides specialized loggers and utilities.
"""

import sys
from pprint import pformat

from config import stash_logger

# Create specialized loggers
client_logger = stash_logger.bind(name="client")
processing_logger = stash_logger.bind(name="processing")


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
            # Use specialized logger if name matches
            if logger_name == "client":
                client_logger.debug(formatted)
            elif logger_name == "processing":
                processing_logger.debug(formatted)
            else:
                # Create new specialized logger
                stash_logger.bind(name=logger_name).debug(formatted)
        else:
            # Use root stash logger
            stash_logger.debug(formatted)
    except Exception as e:
        print(f"Failed to log debug message: {e}", file=sys.stderr)
