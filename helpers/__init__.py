"""Utility functions and classes for the fansly-scraper project."""

from .browser import open_get_started_url, open_url
from .common import (
    batch_list,
    get_post_id_from_request,
    is_valid_post_id,
    open_location,
)
from .timer import Timer, TimerError
from .web import (
    get_file_name_from_url,
    get_flat_qs_dict,
    get_qs_value,
    get_release_info_from_github,
    guess_user_agent,
    split_url,
)


def __getattr__(name: str) -> object:
    """Lazy import for checkkey to avoid loading JSPyBridge (Node.js daemon
    threads) in every process that imports from helpers."""
    if name == "guess_check_key":
        from .checkkey import guess_check_key

        return guess_check_key
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Timer",
    "TimerError",
    "batch_list",
    "get_file_name_from_url",
    "get_flat_qs_dict",
    "get_post_id_from_request",
    "get_qs_value",
    "get_release_info_from_github",
    "guess_check_key",
    "guess_user_agent",
    "is_valid_post_id",
    "open_get_started_url",
    "open_location",
    "open_url",
    "split_url",
]
