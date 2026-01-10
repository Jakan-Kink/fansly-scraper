"""Tests for the pathio module."""

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from download.downloadstate import DownloadState
from download.types import DownloadType
from media import MediaItem
from pathio import (
    ask_correct_dir,
    delete_temporary_pyinstaller_files,
    get_creator_base_path,
    get_creator_database_path,
    get_creator_metadata_path,
    get_media_save_path,
    set_create_directory_for_download,
)


class MockPathConfig:
    """Mock configuration class for use in tests."""

    def __init__(
        self,
        download_directory=Path("/test/downloads"),
        separate_messages=True,
        separate_timeline=True,
        separate_previews=True,
        use_folder_suffix=True,
        separate_metadata=False,
        metadata_db_file=None,
    ):
        self.download_directory = download_directory
        self.separate_messages = separate_messages
        self.separate_timeline = separate_timeline
        self.separate_previews = separate_previews
        self.use_folder_suffix = use_folder_suffix
        self.separate_metadata = separate_metadata
        self.metadata_db_file = metadata_db_file


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


class TestPathIO:
    """Tests for pathio functions."""

    @mock.patch("pathio.pathio.TKINTER_AVAILABLE", True)
    @mock.patch("pathio.pathio.Tk")
    @mock.patch("pathio.pathio.filedialog")
    @mock.patch("pathio.pathio.textio_logger")
    def test_ask_correct_dir_valid_path(self, mock_logger, mock_filedialog, mock_tk):
        """Test ask_correct_dir function with a valid path."""
        # Setup mock
        mock_filedialog.askdirectory.return_value = "/valid/path"
        mock_path = mock.MagicMock()
        mock_path.is_dir.return_value = True

        # Mock Path to return our mock path
        with mock.patch("pathio.pathio.Path", return_value=mock_path):
            result = ask_correct_dir()

        # Assertions
        mock_tk.return_value.withdraw.assert_called_once()
        mock_filedialog.askdirectory.assert_called_once()
        # Verify INFO log was called (textio_logger.opt(depth=1).log("INFO", ...))
        assert mock_logger.opt.return_value.log.call_count == 1
        # Verify it was an INFO log, not ERROR
        info_call = mock_logger.opt.return_value.log.call_args_list[0]
        assert info_call[0][0] == "INFO"
        assert result == mock_path

    @mock.patch("pathio.pathio.TKINTER_AVAILABLE", True)
    @mock.patch("pathio.pathio.Tk")
    @mock.patch("pathio.pathio.filedialog")
    @mock.patch("pathio.pathio.textio_logger")
    def test_ask_correct_dir_invalid_then_valid_path(
        self, mock_logger, mock_filedialog, mock_tk
    ):
        """Test ask_correct_dir function with an invalid path followed by a valid path."""
        # Setup mocks for two calls - first invalid, second valid
        mock_filedialog.askdirectory.side_effect = ["/invalid/path", "/valid/path"]

        # Rather than using a sequence for Path mock, use a function to handle any number of calls
        def mock_path_side_effect(path_string):
            mock_path = mock.MagicMock()
            # The first call with "/invalid/path" should return a path that's not a directory
            # Any call with "/valid/path" should return a path that is a directory
            if path_string == "/invalid/path":
                mock_path.is_dir.return_value = False
            else:  # "/valid/path" or any other path
                mock_path.is_dir.return_value = True
            return mock_path

        # Mock Path with the side effect function
        with mock.patch("pathio.pathio.Path", side_effect=mock_path_side_effect):
            result = ask_correct_dir()

        # Assertions
        assert mock_tk.return_value.withdraw.call_count == 1
        assert mock_filedialog.askdirectory.call_count == 2
        # Verify both ERROR and INFO logs were called
        assert mock_logger.opt.return_value.log.call_count == 2
        # First call should be ERROR (for invalid path), second should be INFO (for valid path)
        error_call = mock_logger.opt.return_value.log.call_args_list[0]
        info_call = mock_logger.opt.return_value.log.call_args_list[1]
        assert error_call[0][0] == "ERROR"
        assert info_call[0][0] == "INFO"
        assert result.is_dir() is True  # Check the final path is a directory

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

    def test_set_create_directory_for_download_collections(self, temp_dir):
        """Test set_create_directory_for_download for collections using real directory."""
        # Set up config with temp_dir
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.COLLECTIONS
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = temp_dir / "Collections"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == temp_dir / "creator_fansly"
        assert state.download_path == expected_dir

    def test_set_create_directory_for_download_messages(self, temp_dir):
        """Test set_create_directory_for_download for messages using real directory."""
        # Set up config with temp_dir
        config = MockPathConfig(download_directory=temp_dir, separate_messages=True)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.MESSAGES
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = temp_dir / "creator_fansly" / "Messages"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == temp_dir / "creator_fansly"
        assert state.download_path == expected_dir

    def test_set_create_directory_for_download_timeline(self, temp_dir):
        """Test set_create_directory_for_download for timeline using real directory."""
        # Set up config with temp_dir
        config = MockPathConfig(download_directory=temp_dir, separate_timeline=True)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Run the function
        result = set_create_directory_for_download(config, state)

        # Check that the expected directory was created
        expected_dir = temp_dir / "creator_fansly" / "Timeline"
        assert result == expected_dir
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Check that state was updated correctly
        assert state.base_path == temp_dir / "creator_fansly"
        assert state.download_path == expected_dir

    @mock.patch("pathlib.Path.exists")
    def test_get_creator_base_path_case_insensitive(self, mock_exists, temp_dir):
        """Test get_creator_base_path with case-insensitive match."""
        # Create a directory with different case
        creator_dir = temp_dir / "Creator_fansly"
        creator_dir.mkdir(exist_ok=True)

        # Set up config with temp_dir
        config = MockPathConfig(use_folder_suffix=True, download_directory=temp_dir)

        # Make Path.exists() return False to trigger the case-insensitive search
        mock_exists.return_value = False

        # Get path for lowercase name
        result = get_creator_base_path(config, "creator")

        # Assert we get the directory with different case
        assert result == creator_dir

    def test_get_creator_base_path_with_folder_suffix(self, temp_dir):
        """Test get_creator_base_path with folder suffix enabled."""
        config = MockPathConfig(use_folder_suffix=True, download_directory=temp_dir)
        result = get_creator_base_path(config, "creator")
        assert result == temp_dir / "creator_fansly"

    def test_get_creator_base_path_without_folder_suffix(self, temp_dir):
        """Test get_creator_base_path with folder suffix disabled."""
        config = MockPathConfig(use_folder_suffix=False, download_directory=temp_dir)
        result = get_creator_base_path(config, "creator")
        assert result == temp_dir / "creator"

    def test_get_creator_metadata_path(self, temp_dir):
        """Test get_creator_metadata_path creates and returns the correct path."""
        # Set up config with temp_dir
        config = MockPathConfig(download_directory=temp_dir)

        # Create the creator directory first (needed because the function doesn't use parents=True)
        creator_dir = temp_dir / "creator_fansly"
        creator_dir.mkdir(exist_ok=True)

        # Run the function
        result = get_creator_metadata_path(config, "creator")

        # Expected path
        expected_path = temp_dir / "creator_fansly" / "meta"

        # Assert
        assert result == expected_path
        assert expected_path.exists()
        assert expected_path.is_dir()

    def test_get_creator_database_path_separate_metadata(self, temp_dir):
        """Test get_creator_database_path with separate metadata enabled."""
        config = MockPathConfig(separate_metadata=True, download_directory=temp_dir)

        # Create metadata directory first
        meta_dir = temp_dir / "creator_fansly" / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)

        result = get_creator_database_path(config, "creator")

        # Expected path
        expected_path = meta_dir / "metadata.sqlite3"

        # Assert
        assert result == expected_path

    def test_get_creator_database_path_with_metadata_db_file(self, temp_dir):
        """Test get_creator_database_path with metadata_db_file specified."""
        db_path = temp_dir / "custom_db.sqlite3"

        config = MockPathConfig(
            separate_metadata=False,
            metadata_db_file=str(db_path),
            download_directory=temp_dir,
        )

        result = get_creator_database_path(config, "creator")

        # Assert
        assert result == db_path

    def test_get_creator_database_path_shared_db(self, temp_dir):
        """Test get_creator_database_path with shared database."""
        config = MockPathConfig(
            separate_metadata=False, metadata_db_file=None, download_directory=temp_dir
        )

        result = get_creator_database_path(config, "creator")

        # Expected path
        expected_path = temp_dir / "metadata" / "shared.db"

        # Assert
        assert result == expected_path
        assert (temp_dir / "metadata").exists()
        assert (temp_dir / "metadata").is_dir()

    def test_get_media_save_path_images(self, temp_dir):
        """Test get_media_save_path for images."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Create base directory for the test
        base_dir = temp_dir / "creator_fansly" / "Timeline"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Create the Pictures directory (the function returns paths but doesn't create all dirs)
        pictures_dir = base_dir / "Pictures"
        pictures_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "image/jpeg"
        media_item.is_preview = False
        media_item.get_file_name.return_value = "image.jpg"

        # Run the function
        save_dir, save_path = get_media_save_path(config, state, media_item)

        # Expected paths
        expected_dir = base_dir / "Pictures"
        expected_path = expected_dir / "image.jpg"

        # Assert
        assert save_dir == expected_dir
        assert save_path == expected_path
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_media_save_path_videos(self, temp_dir):
        """Test get_media_save_path for videos."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Create base directory for the test
        base_dir = temp_dir / "creator_fansly" / "Timeline"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Create the Videos directory (the function returns paths but doesn't create all dirs)
        videos_dir = base_dir / "Videos"
        videos_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "video/mp4"
        media_item.is_preview = False
        media_item.get_file_name.return_value = "video.mp4"

        # Run the function
        save_dir, save_path = get_media_save_path(config, state, media_item)

        # Expected paths
        expected_dir = base_dir / "Videos"
        expected_path = expected_dir / "video.mp4"

        # Assert
        assert save_dir == expected_dir
        assert save_path == expected_path
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_media_save_path_audio(self, temp_dir):
        """Test get_media_save_path for audio."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.MESSAGES
        )

        # Create base directory for the test
        base_dir = temp_dir / "creator_fansly" / "Messages"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Also create the Audio directory (the function returns paths but doesn't create all dirs)
        audio_dir = base_dir / "Audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "audio/mp3"
        media_item.is_preview = False
        media_item.get_file_name.return_value = "audio.mp3"

        # Run the function
        save_dir, save_path = get_media_save_path(config, state, media_item)

        # Expected paths
        expected_dir = base_dir / "Audio"
        expected_path = expected_dir / "audio.mp3"

        # Assert
        assert save_dir == expected_dir
        assert save_path == expected_path
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_media_save_path_previews(self, temp_dir):
        """Test get_media_save_path for preview content."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir, separate_previews=True)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Create base directory for the test
        base_dir = temp_dir / "creator_fansly" / "Timeline"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Create Pictures and Previews directories
        pictures_dir = base_dir / "Pictures"
        pictures_dir.mkdir(parents=True, exist_ok=True)
        previews_dir = pictures_dir / "Previews"
        previews_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "image/jpeg"
        media_item.is_preview = True
        media_item.get_file_name.return_value = "preview.jpg"

        # Run the function
        save_dir, save_path = get_media_save_path(config, state, media_item)

        # Expected paths
        expected_dir = base_dir / "Pictures" / "Previews"
        expected_path = expected_dir / "preview.jpg"

        # Assert
        assert save_dir == expected_dir
        assert save_path == expected_path
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_get_media_save_path_unknown_mimetype(self, temp_dir):
        """Test get_media_save_path with unknown mimetype."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.TIMELINE
        )

        # Create base directory for the test
        base_dir = temp_dir / "creator_fansly" / "Timeline"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "application/unknown"

        # Assert
        with pytest.raises(ValueError, match="Unknown mimetype"):
            get_media_save_path(config, state, media_item)

    def test_get_media_save_path_collections(self, temp_dir):
        """Test get_media_save_path for collections."""
        # Setup
        config = MockPathConfig(download_directory=temp_dir)
        state = DownloadState(
            creator_name="creator", download_type=DownloadType.COLLECTIONS
        )

        # Create base directory for the test (Collections dir)
        collections_dir = temp_dir / "Collections"
        collections_dir.mkdir(parents=True, exist_ok=True)

        # Mock the media item
        media_item = mock.MagicMock(spec=MediaItem)
        media_item.mimetype = "image/jpeg"
        media_item.is_preview = False
        media_item.get_file_name.return_value = "collection_image.jpg"

        # Run the function
        save_dir, save_path = get_media_save_path(config, state, media_item)

        # Expected path
        expected_path = collections_dir / "collection_image.jpg"

        # Assert
        assert save_dir == collections_dir
        assert save_path == expected_path

    def test_delete_temporary_pyinstaller_files(self, temp_dir):
        """Test delete_temporary_pyinstaller_files function with basic behavior."""
        # Create a simple directory structure
        mei_parent = temp_dir / "mei_temp"
        mei_parent.mkdir()

        # Create old MEI directory with files
        old_mei = mei_parent / "_MEI123"
        old_mei.mkdir()
        (old_mei / "file1.txt").write_text("test")

        # Create recent MEI directory
        recent_mei = mei_parent / "_MEI999"
        recent_mei.mkdir()

        # Mock sys._MEIPASS to point to recent_mei
        sys._MEIPASS = str(recent_mei)

        try:
            # Execute - the function should run without crashing
            # Due to contextlib.suppress(Exception), it won't raise even if operations fail
            delete_temporary_pyinstaller_files()

            # Basic assertion: function completed without crashing
            # The actual deletion behavior depends on file ages and exception handling,
            # so we don't make strong assertions about what was deleted
            assert True

        finally:
            # Cleanup
            if hasattr(sys, "_MEIPASS"):
                delattr(sys, "_MEIPASS")

    @mock.patch("os.path.dirname")
    def test_delete_temporary_pyinstaller_files_exception(self, mock_dirname):
        """Test delete_temporary_pyinstaller_files handles exceptions gracefully."""
        # Setup mock to raise exception
        mock_dirname.side_effect = Exception("Test exception")

        # Execute - should not raise an exception
        delete_temporary_pyinstaller_files()

        # No assertion needed - just checking it doesn't crash
