"""Console Output"""

# Re-exports
from .textio import (
    LOG_FILE_NAME,
    clear_terminal,
    input_enter_close,
    input_enter_continue,
    print_config,
    print_debug,
    print_error,
    print_info,
    print_info_highlight,
    print_update,
    print_warning,
    set_window_title,
)

# from textio import *
__all__ = [
    "LOG_FILE_NAME",
    "print_config",
    "print_debug",
    "print_error",
    "print_info",
    "print_info_highlight",
    "print_update",
    "print_warning",
    "input_enter_close",
    "input_enter_continue",
    "clear_terminal",
    "set_window_title",
]
