"""Download Modes"""

from enum import auto

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
