"""FactoryBoy factories for DownloadState and related download objects.

This module provides factories for creating test instances of download state objects
using FactoryBoy. These factories create real download state objects with sensible defaults,
replacing the need for MagicMock usage in tests.

Usage:
    from tests.fixtures.download import DownloadStateFactory

    # Create a basic download state for testing
    state = DownloadStateFactory()

    # Create a state with specific values
    state = DownloadStateFactory(
        creator_name="testuser",
        creator_id="12345"
    )
"""

from pathlib import Path

from factory import Factory, LazyFunction, Sequence

from download.core import DownloadState, GlobalState
from download.downloadstate import DownloadType


class DownloadStateFactory(Factory):
    """Factory for DownloadState instances.

    Creates DownloadState instances with realistic defaults.
    Override any fields when creating instances.

    Example:
        # Basic download state
        state = DownloadStateFactory()

        # State for specific creator
        state = DownloadStateFactory(
            creator_name="mycreator",
            creator_id="123456789"
        )

        # State with paths configured
        state = DownloadStateFactory(
            base_path=Path("/tmp/test"),
            download_path=Path("/tmp/test/mycreator")
        )
    """

    class Meta:
        model = DownloadState

    # Download type
    download_type = DownloadType.NOTSET

    # Creator state
    creator_name = None
    creator_id = None
    following = False
    subscribed = False

    # Paths
    base_path = None
    download_path = None
    fetched_timeline_duplication = False

    # History tracking - use LazyFunction to create new sets for each instance
    recent_audio_media_ids = LazyFunction(set)
    recent_photo_media_ids = LazyFunction(set)
    recent_video_media_ids = LazyFunction(set)
    recent_audio_hashes = LazyFunction(set)
    recent_photo_hashes = LazyFunction(set)
    recent_video_hashes = LazyFunction(set)

    walls = LazyFunction(set)

    # Batch tracking
    current_batch_duplicates = 0

    # GlobalState parent fields (inherited from GlobalState)
    duplicate_count = 0
    pic_count = 0
    vid_count = 0
    total_message_items = 0
    total_timeline_pictures = 0
    total_timeline_videos = 0


class DownloadStateFactoryWithCreator(DownloadStateFactory):
    """Factory for DownloadState instances with creator configuration.

    This is a specialized factory for creating states with creator information.

    Example:
        state = DownloadStateFactoryWithCreator(creator_name="alice")
        # Automatically gets creator_id and paths set up
    """

    creator_name = Sequence(lambda n: f"creator_{n}")
    creator_id = Sequence(lambda n: str(100000000000000000 + n))
    base_path = LazyFunction(lambda: Path("/tmp/test_downloads"))
    download_path = LazyFunction(
        lambda: Path("/tmp/test_downloads/creator")
    )  # Will be updated based on creator_name in practice


class GlobalStateFactory(Factory):
    """Factory for GlobalState instances.

    Creates GlobalState instances with realistic defaults for testing.
    Override any fields when creating instances.

    Example:
        # Basic global state
        state = GlobalStateFactory()

        # State with specific counts
        state = GlobalStateFactory(
            duplicate_count=5,
            pic_count=10,
            vid_count=3
        )
    """

    class Meta:
        model = GlobalState

    # Counters
    duplicate_count = 0
    pic_count = 0
    vid_count = 0

    total_message_items = 0
    total_timeline_pictures = 0
    total_timeline_videos = 0


__all__ = [
    "DownloadStateFactory",
    "DownloadStateFactoryWithCreator",
    "GlobalStateFactory",
]
