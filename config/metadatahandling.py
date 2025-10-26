"""Metadata Handling"""

from enum import auto

from strenum import StrEnum


class MetadataHandling(StrEnum):
    NOTSET = auto()
    ADVANCED = auto()
    SIMPLE = auto()

    @classmethod
    def _missing_(cls, value):
        """Handle case-insensitive lookup of enum values."""
        if isinstance(value, str):
            # Try to match case-insensitively
            for member in cls:
                if member.lower() == value.lower():
                    return member
        return None
