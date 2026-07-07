"""Tests for the pathio module."""

from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata import Media
from pathio import (
    ask_correct_dir,
    get_creator_base_path,
    get_creator_metadata_path,
    get_media_save_path,
    get_stash_path,
    set_create_directory_for_download,
)
from tests.fixtures.metadata import MediaFactory


class MockPathConfig:
    """Mock configuration class for use in tests."""

    def __init__(
        self,
        download_directory=Path("/test/downloads"),
        separate_messages=True,
        separate_timeline=True,
        separate_previews=True,
        use_folder_suffix=True,
        stash_mapped_path=None,
        stash_override_dldir_w_mapped=False,
    ):
        self.download_directory = download_directory
        self.separate_messages = separate_messages
        self.separate_timeline = separate_timeline
        self.separate_previews = separate_previews
        self.use_folder_suffix = use_folder_suffix
        self.stash_mapped_path = stash_mapped_path
        self.stash_override_dldir_w_mapped = stash_override_dldir_w_mapped


class TestPathIO:
    """Tests for pathio functions."""

    def test_set_create_directory_for_download_no_download_dir(self):
        """Test set_create_directory_for_download with missing download directory."""
        config = MockPathConfig(download_directory=None)
        state = DownloadState(
            creator_name="test_creator", download_type=DownloadType.MESSAGES
        )

        with pytest.raises(RuntimeError, match="download directory not set"):
            set_create_directory_for_download(config, state)

    def test_set_create_directory_for_download_no_creator_name(self):
        """Test set_create_directory_for_download with missing creator name."""
        config = MockPathConfig()
        state = DownloadState(creator_name=None, download_type=DownloadType.MESSAGES)

        with pytest.raises(RuntimeError, match="creator name not set"):
            set_create_directory_for_download(config, state)

    def test_set_create_directory_for_download_collections(self, tmp_path):
        """Test set_create_directory_for_download for collections using real directory."""
        # Set up config with tmp_path
        config = MockPathConfig(download_directory=tmp_path)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.COLLECTIONS
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = tmp_path / "Collections"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == tmp_path / "creator_fansly"
        assert state.download_path == expected_dir

    def test_set_create_directory_for_download_messages(self, tmp_path):
        """Test set_create_directory_for_download for messages using real directory."""
        # Set up config with tmp_path
        config = MockPathConfig(download_directory=tmp_path, separate_messages=True)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.MESSAGES
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = tmp_path / "creator_fansly" / "Messages"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == tmp_path / "creator_fansly"
        assert state.download_path == expected_dir

    def test_set_create_directory_for_download_timeline(self, tmp_path):
        """Test set_create_directory_for_download for timeline using real directory."""
        # Set up config with tmp_path
        config = MockPathConfig(download_directory=tmp_path, separate_timeline=True)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = tmp_path / "creator_fansly" / "Timeline"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == tmp_path / "creator_fansly"
        assert state.download_path == expected_dir

    def test_get_creator_base_path_case_insensitive(self, tmp_path):
        """Real ``Creator_fansly`` dir is matched case-insensitively for ``creator``.

        Uses a REAL ``tmp_path`` filesystem (real ``iterdir``/``is_dir``/``name``
        comparison run for real). The lone narrowly-scoped stub is on the exact
        lowercase ``creator_fansly`` target's ``exists()`` returning ``False``:
        macOS's case-insensitive filesystem reports ``creator_fansly`` as already
        existing when ``Creator_fansly`` is on disk, so the production
        case-insensitive search branch (pathio.pathio:138-142) is otherwise
        unreachable on this platform. Every other path's ``exists()`` resolves
        against the real filesystem.
        """
        # Real directory created with a different case than the lookup name.
        creator_dir = tmp_path / "Creator_fansly"
        creator_dir.mkdir(exist_ok=True)

        config = MockPathConfig(use_folder_suffix=True, download_directory=tmp_path)

        target = tmp_path / "creator_fansly"
        real_exists = Path.exists

        def fake_exists(self):
            # Only the case-folded target reports missing; all else is real.
            if self == target:
                return False
            return real_exists(self)

        with mock.patch.object(Path, "exists", fake_exists):
            result = get_creator_base_path(config, "creator")

        # Real iterdir loop returns the differently-cased directory.
        assert result == creator_dir

    def test_get_creator_base_path_with_folder_suffix(self, tmp_path):
        """Test get_creator_base_path with folder suffix enabled."""
        config = MockPathConfig(use_folder_suffix=True, download_directory=tmp_path)
        result = get_creator_base_path(config, "creator")
        assert result == tmp_path / "creator_fansly"

    def test_get_creator_base_path_without_folder_suffix(self, tmp_path):
        """Test get_creator_base_path with folder suffix disabled."""
        config = MockPathConfig(use_folder_suffix=False, download_directory=tmp_path)
        result = get_creator_base_path(config, "creator")
        assert result == tmp_path / "creator"

    def test_get_creator_metadata_path(self, tmp_path):
        """Test get_creator_metadata_path creates and returns the correct path."""
        # Set up config with tmp_path
        config = MockPathConfig(download_directory=tmp_path)

        # Create the creator directory first (needed because the function doesn't use parents=True)
        creator_dir = tmp_path / "creator_fansly"
        creator_dir.mkdir(exist_ok=True)

        # Run the function
        result = get_creator_metadata_path(config, "creator")

        # Expected path
        expected_path = tmp_path / "creator_fansly" / "meta"

        # Assert
        assert result == expected_path
        assert expected_path.exists()
        assert expected_path.is_dir()

    @pytest.mark.parametrize(
        ("download_type", "mimetype", "is_preview", "separate_previews", "subdirs"),
        [
            (
                DownloadType.TIMELINE,
                "image/jpeg",
                False,
                True,
                ("Timeline", "Pictures"),
            ),
            (DownloadType.TIMELINE, "video/mp4", False, True, ("Timeline", "Videos")),
            (DownloadType.MESSAGES, "audio/mp3", False, True, ("Messages", "Audio")),
            (
                DownloadType.TIMELINE,
                "image/jpeg",
                True,
                True,
                ("Timeline", "Pictures", "Previews"),
            ),
            (
                DownloadType.TIMELINE,
                "image/jpeg",
                True,
                False,
                ("Timeline", "Pictures"),
            ),
        ],
        ids=[
            "timeline-image-Pictures",
            "timeline-video-Videos",
            "messages-audio-Audio",
            "timeline-preview-Pictures/Previews",
            "timeline-preview-no-separate-Pictures",
        ],
    )
    def test_get_media_save_path_per_media_type(
        self,
        tmp_path,
        download_type,
        mimetype,
        is_preview,
        separate_previews,
        subdirs,
    ):
        """get_media_save_path classifies a real Media by mimetype/preview.

        Uses a real ``Media`` from ``MediaFactory`` (not ``MagicMock(spec=Media)``)
        so the production mimetype branch in ``get_media_save_path`` AND the real
        ``Media.get_file_name`` (filename composition from ``createdAt`` / id /
        ``file_extension`` / preview marker) both run end-to-end.
        """
        config = MockPathConfig(
            download_directory=tmp_path, separate_previews=separate_previews
        )
        state = DownloadState(creator_name="creator", download_type=download_type)

        # Real Media: pass fields as factory kwargs (no attribute-mock hacks).
        media_item: Media = MediaFactory.build(
            mimetype=mimetype,
            is_preview=is_preview,
            file_extension="dat",
            createdAt=datetime(2024, 1, 15, 12, 30, tzinfo=UTC),
        )
        # The real classification builds save_dir; the function does not create
        # intermediate dirs, so create the expected tree under real tmp_path.
        expected_dir = tmp_path / "creator_fansly"
        for part in subdirs:
            expected_dir = expected_dir / part
        expected_dir.mkdir(parents=True, exist_ok=True)

        # Real get_file_name() runs — derive the expected filename from it.
        expected_name = media_item.get_file_name(for_preview=is_preview)
        expected_path = expected_dir / expected_name

        save_dir, save_path = get_media_save_path(config, state, media_item)

        assert save_dir == expected_dir
        assert save_path == expected_path
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_media_save_path_collections(self, tmp_path):
        """Collections mode saves directly under the Collections dir (no type split)."""
        config = MockPathConfig(download_directory=tmp_path)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.COLLECTIONS
        )

        collections_dir = tmp_path / "Collections"
        collections_dir.mkdir(parents=True, exist_ok=True)

        media_item: Media = MediaFactory.build(
            mimetype="image/jpeg",
            is_preview=False,
            file_extension="jpg",
            createdAt=datetime(2024, 1, 15, 12, 30, tzinfo=UTC),
        )
        expected_path = collections_dir / media_item.get_file_name(for_preview=False)

        save_dir, save_path = get_media_save_path(config, state, media_item)

        assert save_dir == collections_dir
        assert save_path == expected_path

    def test_get_media_save_path_unknown_mimetype(self, tmp_path):
        """A real Media with an unclassifiable mimetype raises ValueError."""
        config = MockPathConfig(download_directory=tmp_path)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        media_item: Media = MediaFactory.build(
            mimetype="application/unknown",
            is_preview=False,
            file_extension="bin",
        )

        with pytest.raises(ValueError, match="Unknown mimetype"):
            get_media_save_path(config, state, media_item)

    async def test_ask_correct_dir_valid(self, tmp_path):
        """TTY + valid input: returns the parsed directory."""
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_session = mock.MagicMock()
        mock_session.prompt_async = mock.AsyncMock(return_value=str(tmp_path))
        with (
            mock.patch("pathio.pathio.sys.stdin", mock_stdin),
            mock.patch("pathio.pathio.PromptSession", return_value=mock_session),
        ):
            result = await ask_correct_dir()
        assert result == tmp_path

    async def test_ask_correct_dir_invalid_then_valid(self, tmp_path):
        """Invalid path on first prompt → loop → valid path on second."""
        valid_dir = tmp_path / "valid"
        valid_dir.mkdir()
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_session = mock.MagicMock()
        mock_session.prompt_async = mock.AsyncMock(
            side_effect=["/nonexistent", str(valid_dir)]
        )
        with (
            mock.patch("pathio.pathio.sys.stdin", mock_stdin),
            mock.patch("pathio.pathio.PromptSession", return_value=mock_session),
        ):
            result = await ask_correct_dir()
        assert result == valid_dir

    async def test_ask_correct_dir_keyboard_interrupt(self):
        """KeyboardInterrupt during prompt re-raises after logging."""
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_session = mock.MagicMock()
        mock_session.prompt_async = mock.AsyncMock(side_effect=KeyboardInterrupt)
        with (
            mock.patch("pathio.pathio.sys.stdin", mock_stdin),
            mock.patch("pathio.pathio.PromptSession", return_value=mock_session),
            pytest.raises(KeyboardInterrupt),
        ):
            await ask_correct_dir()

    async def test_ask_correct_dir_non_interactive_raises(self):
        """No TTY → RuntimeError pointing at config.yaml."""
        with (
            mock.patch("pathio.pathio.sys.stdin", None),
            pytest.raises(RuntimeError, match="unable to prompt"),
        ):
            await ask_correct_dir()

    def test_get_creator_base_path_no_download_dir(self):
        """Line 157: download_directory is None → RuntimeError."""
        config = MockPathConfig(download_directory=None)
        with pytest.raises(RuntimeError, match="not set"):
            get_creator_base_path(config, "creator")


class TestGetStashPath:
    """Tests for get_stash_path() path-translation helper."""

    @pytest.mark.parametrize(
        ("download_directory", "stash_mapped_path", "local", "expected"),
        [
            (
                Path("/home/user/downloads"),
                None,
                Path("/home/user/downloads/alice_fansly/Timeline"),
                "/home/user/downloads/alice_fansly/Timeline",
            ),
            (
                Path("/home/user/downloads"),
                Path("/data/fansly"),
                Path("/home/user/downloads/alice_fansly/Timeline"),
                "/data/fansly/alice_fansly/Timeline",
            ),
            (
                Path("/home/user/downloads"),
                Path("/data/fansly"),
                Path("/other/location/alice_fansly"),
                "/other/location/alice_fansly",
            ),
            (
                None,
                Path("/data/fansly"),
                Path("/home/user/downloads/alice_fansly"),
                "/home/user/downloads/alice_fansly",
            ),
            (
                Path("/mnt/storage"),
                Path("/stash/library"),
                Path("/mnt/storage/creator_fansly/Timeline/Videos"),
                "/stash/library/creator_fansly/Timeline/Videos",
            ),
        ],
        ids=[
            "no_mapping_returns_local_path",
            "mapping_replaces_prefix",
            "no_prefix_match_returns_local",
            "download_directory_none_returns_local",
            "mapping_preserves_subdirectory_structure",
        ],
    )
    def test_get_stash_path(
        self,
        download_directory: Path | None,
        stash_mapped_path: Path | None,
        local: Path,
        expected: str,
    ) -> None:
        """get_stash_path translation table.

        no_mapping: stash_mapped_path None → the original path string.
        replaces_prefix: mapping set + prefix matches → prefix substituted.
        no_prefix_match: mapping set but path outside download_directory →
        original path string unchanged.
        download_directory_none: no substitution attempted.
        preserves_subdir: nested subdirectories (Timeline, Videos, etc.) are
        preserved after remapping.
        """
        config = MockPathConfig(
            download_directory=download_directory,
            stash_mapped_path=stash_mapped_path,
        )
        assert get_stash_path(local, config) == expected
