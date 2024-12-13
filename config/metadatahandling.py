"""Metadata Handling"""

from enum import auto

from strenum import StrEnum


class MetadataHandling(StrEnum):
    NOTSET = auto()
    ADVANCED = auto()
    SIMPLE = auto()
