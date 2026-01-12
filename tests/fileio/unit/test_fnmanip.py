"""Unit tests for the fnmanip module."""

from pathlib import Path
from unittest.mock import patch

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

    def test_get_hash_for_image(self, tmp_path):
        """Test get_hash_for_image with a real image file."""
        # Create a real 1x1 pixel PNG image
        from PIL import Image

        image_path = tmp_path / "test_image.png"
        img = Image.new("RGB", (1, 1), color="red")
        img.save(image_path)

        # Only mock imagehash.phash to control the return value
        with patch("fileio.fnmanip.imagehash.phash", return_value="test_hash_value"):
            result = get_hash_for_image(image_path)
            assert result == "test_hash_value"

    def test_get_hash_for_image_verify_fails(self, tmp_path):
        """Test get_hash_for_image when image file is corrupted."""
        # Create a file with invalid image data
        invalid_image = tmp_path / "invalid.jpg"
        invalid_image.write_bytes(b"This is not a valid image file")

        # Real PIL will fail to verify this
        with pytest.raises(RuntimeError, match="Failed to verify image"):
            get_hash_for_image(invalid_image)

    def test_get_hash_for_image_hash_returns_none(self, tmp_path):
        """Test get_hash_for_image when imagehash.phash returns None."""
        # Create a real image
        from PIL import Image

        image_path = tmp_path / "test_image.png"
        img = Image.new("RGB", (1, 1), color="blue")
        img.save(image_path)

        # Mock imagehash.phash to return None
        with (
            patch("fileio.fnmanip.imagehash.phash", return_value=None),
            pytest.raises(RuntimeError, match="Hash generation returned None"),
        ):
            get_hash_for_image(image_path)

    def test_get_hash_for_image_hash_fails(self, tmp_path):
        """Test get_hash_for_image when imagehash.phash raises exception."""
        # Create a real image
        from PIL import Image

        image_path = tmp_path / "test_image.png"
        img = Image.new("RGB", (1, 1), color="green")
        img.save(image_path)

        # Mock imagehash.phash to raise an exception
        with (
            patch(
                "fileio.fnmanip.imagehash.phash", side_effect=Exception("Hash failed")
            ),
            pytest.raises(RuntimeError, match="Failed to hash image"),
        ):
            get_hash_for_image(image_path)

    def test_get_hash_for_image_file_not_found(self, tmp_path):
        """Test get_hash_for_image when the image file doesn't exist."""
        nonexistent_path = tmp_path / "does_not_exist.png"

        with pytest.raises(RuntimeError, match="Failed to verify image"):
            get_hash_for_image(nonexistent_path)


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
