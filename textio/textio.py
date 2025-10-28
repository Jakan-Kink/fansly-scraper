"""Console Output

This module provides user-facing output functions that use the centralized
logging configuration from config/logging.py.

Functions:
- print_* - Convenience functions for different message types
- json_output - For structured JSON logging

Note: All logger configuration is now centralized in config/logging.py.
This module only provides the output functions.
"""

import platform
import shutil
import subprocess
import sys
from time import sleep

from config.logging import json_logger, textio_logger


def json_output(level: int, log_type: str, message: str) -> None:
    """Output JSON-formatted log messages.

    Args:
        level: Log level number (1=INFO, 2=DEBUG)
        log_type: Type/category of log message
        message: The message to log
    """
    # Map old levels to loguru levels
    level_map = {
        1: "INFO",  # Most common, used for normal logging
        2: "DEBUG",  # Used for detailed info like unknown attributes
    }

    # Default to INFO if level not in map
    loguru_level = level_map.get(level, "INFO")

    # Format the message with log_type on first line and message on next
    formatted_message = f"[{log_type}]\n{message}"

    # Use the centralized json logger with mapped level
    json_logger.opt(depth=1).log(loguru_level, formatted_message)


def print_config(message: str) -> None:
    """Print a configuration message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("CONFIG", message)


def print_debug(message: str) -> None:
    """Print a debug message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("DEBUG", message)


def print_error(message: str, number: int = -1) -> None:
    """Print an error message.

    Args:
        message: The message to print.
        number: Optional error number to display.
    """
    if number >= 0:
        # Use loguru's color markup to add the error number in red
        textio_logger.opt(depth=1).log("ERROR", f"<red>[{number}]</red> {message}")
    else:
        textio_logger.opt(depth=1).log("ERROR", message)


def print_info(message: str) -> None:
    """Print an info message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("INFO", message)


def print_info_highlight(message: str) -> None:
    """Print a highlighted info message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("-INFO-", message)


def print_update(message: str) -> None:
    """Print an update message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("UPDATE", message)


def print_warning(message: str) -> None:
    """Print a warning message.

    Args:
        message: The message to print.
    """
    textio_logger.opt(depth=1).log("WARNING", message)


def input_enter_close(interactive: bool) -> None:
    """Asks user for <ENTER> to close and exits the program.
    In non-interactive mode sleeps instead, then exits.
    """
    if interactive:
        input("\nPress <ENTER> to close ...")

    else:
        print("\nExiting in 15 seconds ...")
        sleep(15)

    sys.exit()


def input_enter_continue(interactive: bool) -> None:
    """Asks user for <ENTER> to continue.
    In non-interactive mode sleeps instead.
    """
    if interactive:
        input("\nPress <ENTER> to attempt to continue ...")
    else:
        print("\nContinuing in 15 seconds ...")
        sleep(15)


# clear the terminal based on the operating system
def clear_terminal() -> None:
    system = platform.system()

    if system == "Windows":
        cmd_path = shutil.which("cmd")
        if cmd_path:
            subprocess.call([cmd_path, "/c", "cls"])

    else:  # Linux & macOS
        clear_path = shutil.which("clear")
        if clear_path:
            subprocess.call([clear_path])


# cross-platform compatible, re-name downloaders terminal output window title
def set_window_title(title: str) -> None:
    current_platform = platform.system()

    if current_platform == "Windows":
        cmd_path = shutil.which("cmd")
        if cmd_path:
            subprocess.call([cmd_path, "/c", "title", title])

    elif current_platform in {"Linux", "Darwin"}:
        printf_path = shutil.which("printf")
        if printf_path:
            subprocess.call([printf_path, rf"\33]0;{title}\a"])
