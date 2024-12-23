"""Console Output"""

import os
import platform
import subprocess
import sys
from functools import partialmethod
from pathlib import Path
from time import sleep

from loguru import logger

from .logging import SizeTimeRotatingHandler

LOG_FILE_NAME: str = "fansly_downloader_ng.log"
JSON_FILE_NAME: str = "fansly_downloader_ng_json.log"


# most of the time, we utilize this to display colored output rather than logging or prints
def output(level: int, log_type: str, color: str, message: str) -> None:
    try:
        logger.level(log_type, no=level, color=color)

    except TypeError:
        # level failsafe
        pass
    except ValueError:
        # color failsafe
        pass

    logger.__class__.type = partialmethod(logger.__class__.log, log_type)

    logger.remove()

    logger.add(
        sys.stdout,
        format="<level>{level}</level> | <white>{time:HH:mm}</white> <level>|</level><light-white>| {message}</light-white>",
        level=log_type,
        filter=lambda record: not record["extra"].get("json", False),
    )
    logger.add(
        Path.cwd() / LOG_FILE_NAME,
        encoding="utf-8",
        format="[{level} ] [{time:YYYY-MM-DD} | {time:HH:mm}]: {message}",
        level=log_type,
        filter=lambda record: not record["extra"].get("json", False),
        rotation="1MB",
        retention=5,
        backtrace=True,
        diagnose=True,
    )

    logger.type(message)


def json_output(level: int, log_type: str, message: str) -> None:
    try:
        logger.level(log_type, no=level)

    except TypeError:
        # level failsafe
        pass
    except ValueError:
        # color failsafe
        pass

    logger.__class__.type = partialmethod(logger.__class__.log, log_type)

    logger.remove()

    # Use our custom handler for JSON logs with size and time rotation
    json_handler = SizeTimeRotatingHandler(
        filename=str(Path.cwd() / JSON_FILE_NAME),
        max_bytes=50 * 1000 * 1000,  # 50MB
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
