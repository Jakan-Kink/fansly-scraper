"""Console Output

This module provides user-facing output functions that use the centralized
logging configuration from config/logging.py.

Functions:
- output() - Base function for all output
- print_* - Convenience functions for different message types
- json_output - For structured JSON logging

Note: All logger configuration is now centralized in config/logging.py.
This module only provides the output functions.
"""

import os
import platform
import subprocess
import sys
from time import sleep

# Note: Logger imports are inside functions to avoid circular imports


# most of the time, we utilize this to display colored output rather than logging or prints
def output(level: int, log_type: str, color: str, message: str) -> None:
    """Output a message with color and level.

    Args:
        level: Log level number (1-7)
        log_type: Type/category of log message
        color: Color for the message (used in format string)
        message: The message to log
    """
    # Import here to avoid circular imports
    from config import textio_logger
    from config.logging import _LEVEL_MAP, _LEVEL_VALUES

    # Convert level 1-7 to loguru level name
    if 1 <= level <= 7:
        level = _LEVEL_MAP[level]  # Maps to loguru's levels (10, 20, 30, etc)

    # Convert level number to name
    for name, value in _LEVEL_VALUES.items():
        if value == level:
            level = name
            break

    # Log using the centralized textio logger
    # Level filtering is handled by loguru based on handler's level setting
    textio_logger.log(level, message)


def json_output(level: int, log_type: str, message: str) -> None:
    """Output JSON-formatted log messages.

    Args:
        level: Log level number (1-7)
        log_type: Type/category of log message
        message: The message to log
    """
    # Import here to avoid circular imports
    from config import json_logger
    from config.logging import _LEVEL_MAP, _LEVEL_VALUES

    # Convert level 1-7 to loguru level name
    if 1 <= level <= 7:
        level = _LEVEL_MAP[level]  # Maps to loguru's levels (10, 20, 30, etc)

    # Convert level number to name
    for name, value in _LEVEL_VALUES.items():
        if value == level:
            level = name
            break

    # Log using the centralized json logger
    # Level filtering is handled by loguru based on handler's level setting
    json_logger.log(level, message)


def print_config(message: str) -> None:
    output(5, " Config", "<light-magenta>", message)


def print_debug(message: str) -> None:
    output(7, " DEBUG", "<light-red>", message)


def print_error(message: str, number: int = -1) -> None:
    if number >= 0:
        output(2, f" [{number}]ERROR", "<red>", message)
    else:
        output(2, " ERROR", "<red>", message)


def print_info(message: str) -> None:
    output(1, " Info", "<light-blue>", message)


def print_info_highlight(message: str) -> None:
    output(4, " lnfo", "<light-red>", message)


def print_update(message: str) -> None:
    output(6, " Updater", "<light-green>", message)


def print_warning(message: str) -> None:
    output(3, " WARNING", "<yellow>", message)


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
        os.system("cls")

    else:  # Linux & macOS
        os.system("clear")


# cross-platform compatible, re-name downloaders terminal output window title
def set_window_title(title) -> None:
    current_platform = platform.system()

    if current_platform == "Windows":
        subprocess.call(f"title {title}", shell=True)

    elif current_platform == "Linux" or current_platform == "Darwin":
        subprocess.call(["printf", rf"\33]0;{title}\a"])
