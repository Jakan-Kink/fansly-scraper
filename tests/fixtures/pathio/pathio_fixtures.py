"""Stand-ins for pathio's ``PathConfig`` protocol."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FakePathConfig:
    """Plain-data object satisfying ``pathio.types.PathConfig``."""

    download_directory: Path | None = Path("/test/downloads")
    separate_messages: bool = True
    separate_timeline: bool = True
    separate_previews: bool = True
    use_folder_suffix: bool = True
    stash_mapped_path: Path | None = None
    stash_override_dldir_w_mapped: bool = False
