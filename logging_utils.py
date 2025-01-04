"""Logging utilities shared across modules."""

import os
from functools import partialmethod
from pathlib import Path

from loguru import logger

from textio.logging import SizeTimeRotatingHandler


def get_json_log_path() -> str:
    """Get the path to the JSON log file.

    Returns:
        The path to use for JSON logging, using environment variable if set
    """
    return os.getenv("LOGURU_JSON_LOG_FILE", "fansly_downloader_ng_json.log")


def json_output(level: int, log_type: str, message: str) -> None:
    """Output JSON-formatted log messages with size and time rotation.

    Args:
        level: Log level number
        log_type: Type/category of log message
        message: The message to log
    """
    try:
        logger.level(log_type, no=level)
    except (TypeError, ValueError):
        # level or color failsafe
        pass

    logger.__class__.type = partialmethod(logger.__class__.log, log_type)
    logger.remove()

    # Use our custom handler for JSON logs with size and time rotation
    json_handler = SizeTimeRotatingHandler(
        filename=str(Path.cwd() / get_json_log_path()),
        max_bytes=500 * 1000 * 1000,  # 50MB
        backup_count=20,  # Keep 20 files total
        when="h",
        interval=2,  # Rotate every 2 hours if needed
        compression="gz",
        keep_uncompressed=3,  # Keep last 3 files uncompressed
        encoding="utf-8",
    )
    logger.add(
        json_handler.write,
        format="[ {level} ] [{time:YYYY-MM-DD} | {time:HH:mm}]:\n{message}",
        level=log_type,
        filter=lambda record: record["extra"].get("json", False),
        backtrace=False,
        diagnose=False,
    )

    logger.bind(json=True).type(message)
