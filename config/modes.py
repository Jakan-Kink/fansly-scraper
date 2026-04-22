"""Download Modes"""

from enum import auto
from typing import Any, Self

from strenum import StrEnum


class DownloadMode(StrEnum):
    """Selector for which media scope the downloader processes.

    Members:
        NOTSET:     Sentinel — must be replaced by validation before download runs.
        COLLECTION: Download all content in the "Purchased Media Collection".
        MESSAGES:   Download only the creator's DM attachments.
        NORMAL:     Run Timeline then Messages sequentially (the default).
        SINGLE:     Fetch a single post by post ID (visible in a post's browser URL).
        STORIES:    Download only the creator's active stories.
        TIMELINE:   Download only the creator's timeline posts.
        WALL:       Download only the creator's wall content.
        STASH_ONLY: Process Stash metadata only; skip media downloads.
    """

    NOTSET = auto()
    COLLECTION = auto()
    MESSAGES = auto()
    NORMAL = auto()
    SINGLE = auto()
    STORIES = auto()
    TIMELINE = auto()
    WALL = auto()
    STASH_ONLY = auto()

    @classmethod
    def _missing_(cls, value: Any) -> Self | None:
        """Handle case-insensitive lookup of enum values."""
        if isinstance(value, str):
            # Try to match case-insensitively
            for member in cls:
                if member.lower() == value.lower():
                    return member
        return None
