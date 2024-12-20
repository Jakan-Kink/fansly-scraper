"""Download Types"""

from enum import auto

from strenum import StrEnum


class DownloadType(StrEnum):
    NOTSET = auto()
    COLLECTIONS = auto()
    MESSAGES = auto()
    SINGLE = auto()
    TIMELINE = auto()
    WALL = auto()
