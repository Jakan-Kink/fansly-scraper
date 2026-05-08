"""Path handling types and protocols."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PathConfig(Protocol):
    """Protocol defining the path-related configuration interface."""

    @property
    def download_directory(self) -> Path | None:
        """Get the base download directory."""
        ...

    @property
    def use_folder_suffix(self) -> bool:
        """Whether to use folder suffix."""
        ...

    @property
    def separate_messages(self) -> bool:
        """Whether to separate messages into their own folder."""
        ...

    @property
    def separate_timeline(self) -> bool:
        """Whether to separate timeline content into its own folder."""
        ...

    @property
    def separate_previews(self) -> bool:
        """Whether to separate preview content into its own folder."""
        ...

    @property
    def stash_mapped_path(self) -> Path | None:
        """Stash-visible root path for NFS/Docker path remapping."""
        ...

    @property
    def stash_override_dldir_w_mapped(self) -> bool:
        """If True, get_stash_path returns mapped_path directly (ignores per-creator subfolders)."""
        ...
