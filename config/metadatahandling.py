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
        for member in cls:
            if member.value.lower() == str(value).lower():
                return member
        return None
