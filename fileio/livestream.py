"""File-system utilities for livestream recording."""

from __future__ import annotations

from pathlib import Path


def _append_lines(path: Path, lines: list[str]) -> None:
    """Append *lines* to *path*, opening and closing the file in one operation."""
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)


def _unique_output_path(base: Path, segments_base: Path) -> Path:
    """Return *base* if that slot is free, else the next available ``_part{n}`` variant.

    A slot is considered taken when the output MP4 already exists with data
    **or** when its temp segment dir is still on disk (a prior session that
    crashed before muxing).  This prevents successful reconnects from
    silently overwriting a previously completed recording of the same broadcast.
    """

    def _taken(path: Path) -> bool:
        if path.exists() and path.stat().st_size > 0:
            return True
        temp = segments_base / f"{path.stem}_segments"
        return temp.exists()

    if not _taken(base):
        return base
    n = 2
    while True:
        candidate = base.with_name(f"{base.stem}_part{n}{base.suffix}")
        if not _taken(candidate):
            return candidate
        n += 1
