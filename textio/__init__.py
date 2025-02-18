"""Console Output"""

# Export handler classes from logging module
from .logging import SizeAndTimeRotatingFileHandler, SizeTimeRotatingHandler

# Re-exports
from .textio import (
    clear_terminal,
    input_enter_close,
    input_enter_continue,
    json_output,
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
    "SizeAndTimeRotatingFileHandler",
    "SizeTimeRotatingHandler",
    "clear_terminal",
    "input_enter_close",
    "input_enter_continue",
    "json_output",
    "print_config",
    "print_debug",
    "print_error",
    "print_info",
    "print_info_highlight",
    "print_update",
    "print_warning",
    "set_window_title",
]
