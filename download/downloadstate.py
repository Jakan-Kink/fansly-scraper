"""Program Downloading State Management"""

from dataclasses import dataclass, field
from pathlib import Path

from .globalstate import GlobalState
from .types import DownloadType


@dataclass
class DownloadState(GlobalState):
    # region Fields

    download_type: DownloadType = DownloadType.NOTSET

    # Creator state
    creator_name: str | None = None
    creator_id: str | None = None
    following: bool = False
    subscribed: bool = False

    base_path: Path | None = None
    download_path: Path | None = None
    fetchedTimelineDuplication: bool = False

    # History
    recent_audio_media_ids: set = field(default_factory=set)
    recent_photo_media_ids: set = field(default_factory=set)
    recent_video_media_ids: set = field(default_factory=set)
    recent_audio_hashes: set = field(default_factory=set)
    recent_photo_hashes: set = field(default_factory=set)
    recent_video_hashes: set = field(default_factory=set)

    walls: set = field(default_factory=set)

    # Batch tracking
    current_batch_duplicates: int = 0

    # endregion

    # region Methods

    def download_type_str(self) -> str:
        """Gets `download_type` as a string representation."""
        return str(self.download_type).capitalize()

    def start_batch(self) -> None:
        """Reset batch counters for a new batch of downloads."""
        self.current_batch_duplicates = 0

    def add_duplicate(self) -> None:
        """Increment both global and batch duplicate counters."""
        self.duplicate_count += 1
        self.current_batch_duplicates += 1

    # endregion
