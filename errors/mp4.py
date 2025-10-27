"""MPEG-4 File Processing Errors"""

from typing import Any


class InvalidMP4Error(RuntimeError):
    """This error is raised when an invalid MP4 has been found.

    A file is primarily invalid when it does not have a proper
    header with an "ftyp" FourCC code or smaller than 8 bytes.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
