"""Download Modes"""

from enum import auto
from typing import Any, Self

from strenum import StrEnum


class DownloadMode(StrEnum):
    NOTSET = auto()
    COLLECTION = auto()
    MESSAGES = auto()
    NORMAL = auto()
    SINGLE = auto()
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
