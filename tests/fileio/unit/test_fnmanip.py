"""Unit tests for the fnmanip module."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from errors.mp4 import InvalidMP4Error
from fileio.fnmanip import (
    extract_hash_from_filename,
    extract_media_id,
    extract_old_hash0_from_filename,
    extract_old_hash1_from_filename,
    get_hash_for_image,
    get_hash_for_other_content,
)


class TestExtractors:
    """Tests for the filename extractor functions."""

    def test_extract_media_id(self):
        """Test extract_media_id with valid and invalid filenames."""
        # Valid filenames with IDs
        assert extract_media_id("2023-01-01_at_12-30_UTC_id_123456.jpg") == 123456
        assert extract_media_id("some_prefix_id_789.mp4") == 789
        assert extract_media_id("path/to/file_id_101112.mp4") == 101112
        assert (
            extract_media_id("file_with_multiple_underscores_id_654321.jpg") == 654321
        )

        # Invalid filenames without IDs
        assert extract_media_id("2023-01-01_at_12-30_UTC.jpg") is None
        assert extract_media_id("no_id_here.mp4") is None
        assert extract_media_id("") is None
        assert extract_media_id("id_.jpg") is None
        assert extract_media_id("id_not_numeric.jpg") is None

    def test_extract_old_hash0_from_filename(self):
        """Test extract_old_hash0_from_filename with valid and invalid filenames."""
        # Valid filenames with hash
        assert (
            extract_old_hash0_from_filename("2023-01-01_at_12-30_UTC_hash_abc123.jpg")
            == "abc123"
        )
        assert (
            extract_old_hash0_from_filename("some_prefix_hash_def456.mp4") == "def456"
        )
        assert (
            extract_old_hash0_from_filename("path/to/file_hash_987654321abcdef.mp4")
            == "987654321abcdef"
        )

        # Invalid filenames without hash
        assert extract_old_hash0_from_filename("2023-01-01_at_12-30_UTC.jpg") is None
        assert extract_old_hash0_from_filename("no_hash_here.mp4") is None
        assert extract_old_hash0_from_filename("") is None
        assert extract_old_hash0_from_filename("hash_.jpg") is None

        # Filenames with other hash formats
        assert extract_old_hash0_from_filename("file_hash1_abcdef.jpg") is None
        assert extract_old_hash0_from_filename("file_hash2_123456.jpg") is None

    def test_extract_old_hash1_from_filename(self):
        """Test extract_old_hash1_from_filename with valid and invalid filenames."""
        # Valid filenames with hash1
        assert (
            extract_old_hash1_from_filename("2023-01-01_at_12-30_UTC_hash1_abc123.jpg")
            == "abc123"
        )
        assert (
            extract_old_hash1_from_filename("some_prefix_hash1_def456.mp4") == "def456"
        )
        assert (
            extract_old_hash1_from_filename("path/to/file_hash1_987654321abcdef.mp4")
            == "987654321abcdef"
        )

        # Invalid filenames without hash1
        assert extract_old_hash1_from_filename("2023-01-01_at_12-30_UTC.jpg") is None
        assert extract_old_hash1_from_filename("no_hash_here.mp4") is None
        assert extract_old_hash1_from_filename("") is None
        assert extract_old_hash1_from_filename("hash1_.jpg") is None

        # Filenames with other hash formats
        assert extract_old_hash1_from_filename("file_hash_abcdef.jpg") is None
        assert extract_old_hash1_from_filename("file_hash2_123456.jpg") is None

    def test_extract_hash_from_filename(self):
        """Test extract_hash_from_filename with valid and invalid filenames."""
        # Valid filenames with hash2
        assert (
            extract_hash_from_filename("2023-01-01_at_12-30_UTC_hash2_abc123.jpg")
            == "abc123"
        )
        assert extract_hash_from_filename("some_prefix_hash2_def456.mp4") == "def456"
        assert (
            extract_hash_from_filename("path/to/file_hash2_987654321abcdef.mp4")
            == "987654321abcdef"
        )

        # Invalid filenames without hash2
        assert extract_hash_from_filename("2023-01-01_at_12-30_UTC.jpg") is None
        assert extract_hash_from_filename("no_hash_here.mp4") is None
        assert extract_hash_from_filename("") is None
        assert extract_hash_from_filename("hash2_.jpg") is None

        # Filenames with other hash formats
        assert extract_hash_from_filename("file_hash_abcdef.jpg") is None
        assert extract_hash_from_filename("file_hash1_123456.jpg") is None


class TestImageHash:
    """Tests for the image hashing functions."""

    @patch("fileio.fnmanip.Image")
    def test_get_hash_for_image(self, mock_image_module):
        """Test get_hash_for_image with a valid image."""
        # Create a mock image and hash
        mock_verify_image = MagicMock()
        mock_hash_image = MagicMock()

        # Set up context managers to return different mock images for verify and hash
        mock_verify_ctx = MagicMock()
        mock_verify_ctx.__enter__ = MagicMock(return_value=mock_verify_image)
        mock_verify_ctx.__exit__ = MagicMock(return_value=None)

        mock_hash_ctx = MagicMock()
        mock_hash_ctx.__enter__ = MagicMock(return_value=mock_hash_image)
        mock_hash_ctx.__exit__ = MagicMock(return_value=None)

        # Make Image.open return different context managers for each call
        mock_image_module.open.side_effect = [mock_verify_ctx, mock_hash_ctx]

        # Mock the imagehash.phash function
        mock_phash = MagicMock(return_value="mock_hash_value")

        with patch("fileio.fnmanip.imagehash.phash", mock_phash):
            # Call the function with a mock path
            path = Path("test_image.jpg")
            result = get_hash_for_image(path)

            # Verify the result
            assert result == "mock_hash_value"

            # Verify Image.open was called twice with the path
            assert mock_image_module.open.call_count == 2
            mock_image_module.open.assert_has_calls(
                [
                    call(path),  # First call for verification
                    call(path),  # Second call for hashing
                ]
            )

            # Verify verify() was called on first image
            mock_verify_image.verify.assert_called_once()

            # Verify imagehash.phash was called with second image
            mock_phash.assert_called_once_with(mock_hash_image, hash_size=16)

    @patch("fileio.fnmanip.Image")
    def test_get_hash_for_image_verify_fails(self, mock_image_module):
        """Test get_hash_for_image when image verification fails."""
        # Set up mock for verification
        mock_image = MagicMock()
        mock_image.verify.side_effect = Exception("Verification failed")

        # Set up context manager that should raise during verify
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_image)
        mock_ctx.__exit__ = MagicMock(return_value=None)

        # Make Image.open return our failing context manager
        mock_image_module.open.return_value = mock_ctx

        # Call the function with a mock path
        path = Path("test_image.jpg")
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_image(path)

        # Verify the error message
        assert "Failed to verify image" in str(excinfo.value)
        assert "Verification failed" in str(excinfo.value)

    @patch("fileio.fnmanip.Image")
    def test_get_hash_for_image_hash_fails(self, mock_image_module):
        """Test get_hash_for_image when hashing fails."""
        # Set up mock for verification - should succeed
        mock_verify_image = MagicMock()
        mock_verify_ctx = MagicMock()
        mock_verify_ctx.__enter__ = MagicMock(return_value=mock_verify_image)
        mock_verify_ctx.__exit__ = MagicMock(return_value=None)

        # Set up mock for hashing - should succeed opening but fail hash
        mock_hash_image = MagicMock()
        mock_hash_ctx = MagicMock()
        mock_hash_ctx.__enter__ = MagicMock(return_value=mock_hash_image)
        mock_hash_ctx.__exit__ = MagicMock(return_value=None)

        # Make Image.open return our mocks in sequence
        mock_image_module.open.side_effect = [mock_verify_ctx, mock_hash_ctx]

        # Mock imagehash.phash to raise an exception
        mock_phash = MagicMock(side_effect=Exception("Hash generation failed"))

        with patch("fileio.fnmanip.imagehash.phash", mock_phash):
            path = Path("test_image.jpg")
            with pytest.raises(RuntimeError) as excinfo:
                get_hash_for_image(path)

            assert "Failed to hash image" in str(excinfo.value)
            assert "Hash generation failed" in str(excinfo.value)

    @patch("fileio.fnmanip.Image")
    def test_get_hash_for_image_open_fails(self, mock_image_module):
        """Test get_hash_for_image when the image cannot be opened."""
        # Make Image.open raise an OSError
        error_msg = "Failed to open file"
        mock_image_module.open.side_effect = OSError(error_msg)

        path = Path("test_image.jpg")
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_image(path)

        assert "Failed to open" in str(excinfo.value)
        assert error_msg in str(excinfo.value)


class TestVideoHash:
    """Tests for the video hashing functions."""

    @patch("fileio.fnmanip.hash_mp4file")
    def test_get_hash_for_other_content(self, mock_hash_mp4file):
        """Test get_hash_for_other_content with a valid file."""
        # Mock hash_mp4file to return a hash
        mock_hash_mp4file.return_value = "mock_hash_value"

        # Call the function with a mock path
        path = Path("test_video.mp4")
        result = get_hash_for_other_content(path)

        # Verify the result
        assert result == "mock_hash_value"

        # Verify hash_mp4file was called with the correct arguments
        mock_hash_mp4file.assert_called_once()
        assert mock_hash_mp4file.call_args[0][1] == path

    @patch("fileio.fnmanip.hash_mp4file")
    def test_get_hash_for_other_content_invalid_mp4(self, mock_hash_mp4file):
        """Test get_hash_for_other_content with an invalid MP4 file."""
        # Make hash_mp4file raise an InvalidMP4Error
        mock_hash_mp4file.side_effect = InvalidMP4Error("Invalid MP4")

        # Call the function with a mock path
        path = Path("test_video.mp4")
        with pytest.raises(InvalidMP4Error) as excinfo:
            get_hash_for_other_content(path)

        # Verify the error was propagated
        assert "Invalid MP4" in str(excinfo.value)

    @patch("fileio.fnmanip.hash_mp4file")
    def test_get_hash_for_other_content_hash_fails(self, mock_hash_mp4file):
        """Test get_hash_for_other_content when hashing fails."""
        # Make hash_mp4file return None
        mock_hash_mp4file.return_value = None

        # Call the function with a mock path
        path = Path("test_video.mp4")
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_other_content(path)

        # Verify the error message
        assert "Failed to generate hash" in str(excinfo.value)

    @patch("fileio.fnmanip.hash_mp4file")
    def test_get_hash_for_other_content_general_error(self, mock_hash_mp4file):
        """Test get_hash_for_other_content with a general error."""
        # Make hash_mp4file raise a general exception
        mock_hash_mp4file.side_effect = Exception("General error")

        # Call the function with a mock path
        path = Path("test_video.mp4")
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_other_content(path)

        # Verify the error message
        assert f"Failed to hash file {path}" in str(excinfo.value)
        assert "General error" in str(excinfo.value)
