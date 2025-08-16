"""Path handling types and protocols."""

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
    def separate_metadata(self) -> bool:
        """Whether to store metadata separately per creator."""
        ...

    @property
    def metadata_db_file(self) -> str | None:
        """Path to the global metadata database file if set."""
        ...
