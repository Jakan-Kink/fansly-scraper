"""MPEG-4 Binary File Manipulation

Kudos to Alfred Gutierrez' (alfg) and Sanjeev Pandey's well-summarizing articles:

https://dev.to/alfg/a-quick-dive-into-mp4-57fo
https://sanjeev-pandey.medium.com/understanding-the-mpeg-4-moov-atom-pseudo-streaming-in-mp4-93935e1b9e9a
"""

__all__ = [
    "MP4Box",
    "get_boxes",
    "hash_mp4box",
    "hash_mp4file",
]


import os
from collections.abc import Callable, Iterable
from io import BufferedReader
from itertools import chain, repeat
from pathlib import Path
from typing import Any

from errors.mp4 import InvalidMP4Error
from helpers.shutdown import raise_if_stopping


# Size field plus type field: the least a box can occupy.
_BOX_HEADER_SIZE = 8


class MP4Box:
    """Represents an MPEG-4 binary box/atom object."""

    def __init__(self, size_bytes: bytes, fourcc_bytes: bytes, position: int) -> None:
        self.position = position
        self.size = int.from_bytes(size_bytes, byteorder="big")
        self.fourcc = MP4Box.convert_to_fourcc(fourcc_bytes)

    def __str__(self) -> str:
        return f"MP4Box ( Position: {self.position}, FourCC: {self.fourcc}, Size: {self.size} )"

    @staticmethod
    def convert_to_fourcc(fourcc_bytes: bytes) -> str:
        fourcc: str = ""

        try:
            fourcc = str(fourcc_bytes, encoding="ascii")

        except UnicodeDecodeError:
            for by in fourcc_bytes:
                # See: http://facweb.cs.depaul.edu/sjost/it212/documents/ascii-pr.htm
                # 32-126 inclusive
                by_str = f"[{by}]" if by < 32 or by > 126 else chr(by)
                fourcc += by_str

        return fourcc


def get_boxes(reader: BufferedReader) -> Iterable[MP4Box]:
    """Yield the top-level boxes of an MPEG-4 file.

    Fewer than 8 trailing bytes cannot hold a box header and are ignored.

    Raises:
        InvalidMP4Error: If the file does not start with ``ftyp`` or a box
            declares a size smaller than its own header.
    """
    position = 0
    first = True
    end = reader.seek(0, os.SEEK_END)
    reader.seek(0, os.SEEK_SET)

    while end - position >= _BOX_HEADER_SIZE:
        size_bytes = reader.read(4)
        fourcc_bytes = reader.read(4)
        size = int.from_bytes(size_bytes, byteorder="big")

        # Cope with wide box sizes
        if size == 1:
            size_bytes = reader.read(8)

        box = MP4Box(
            size_bytes=size_bytes,
            fourcc_bytes=fourcc_bytes,
            position=position,
        )
        if size == 0:
            # A zero size means the box runs to the end of the file.
            box.size = end - position

        if first and box.fourcc != "ftyp":
            raise InvalidMP4Error("File header missing, not an MPEG-4 file.")

        first = False

        if box.size < _BOX_HEADER_SIZE:
            # Without this the position never advances and the walk never ends.
            raise InvalidMP4Error(
                f"Box '{box.fourcc}' at {position} declares size {box.size}."
            )

        position += box.size

        reader.seek(position, os.SEEK_SET)

        yield box


def hash_mp4box(algorithm: Any, reader: BufferedReader, box: MP4Box) -> None:
    """Hashes an MPEG-4 box atom.

    `algorithm` must be a `hashlib` algorithm.
    """
    chunk_size = 1_048_576

    reader.seek(box.position, os.SEEK_SET)

    chunks = box.size // chunk_size
    remainder = box.size - chunks * chunk_size

    for size in chain(repeat(chunk_size, chunks), (remainder,)):
        raise_if_stopping()
        algorithm.update(reader.read(size))


def hash_mp4file(
    algorithm: Any,
    file_name: Path,
    print: Callable | None = None,
    use_broken_algo: bool = False,
) -> str:
    if not file_name.exists():
        raise RuntimeError(f"{file_name} does not exist.")

    file_size = file_name.stat().st_size

    if file_size < 8:
        raise InvalidMP4Error(f"{file_name} is too small to be an MPEG-4 file.")

    if print is not None:
        print(f"File: {file_name}")
        print()

    with Path.open(file_name, "rb") as mp4file:
        try:
            boxes = get_boxes(mp4file)

            for box in boxes:
                if print is not None:
                    print(box)

                if use_broken_algo:
                    if box.fourcc not in {"moov", "mdat"}:
                        hash_mp4box(algorithm, mp4file, box)

                elif box.fourcc not in {"free", "moov"}:
                    hash_mp4box(algorithm, mp4file, box)

            if print is not None:
                print()
                print(f"Hash: {algorithm.hexdigest()}")
                print()

            return algorithm.hexdigest()

        except InvalidMP4Error as ex:
            raise InvalidMP4Error(f"{file_name}: {ex}")
