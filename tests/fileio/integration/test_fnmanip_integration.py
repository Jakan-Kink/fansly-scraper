"""Integration tests for the fnmanip module."""

import imagehash
import pytest
from PIL import Image

from errors.mp4 import InvalidMP4Error
from fileio.fnmanip import (
    extract_media_id,
    get_hash_for_image,
    get_hash_for_other_content,
)


class TestExtractorsIntegration:
    """Integration tests for the filename extractor functions."""

    def test_extract_media_id_with_real_filenames(self):
        """Test extract_media_id with some typical real-world filenames."""
        # Test with more complex real-world filenames
        assert extract_media_id("2023-04-15_at_09-30_UTC_id_1234567.jpg") == 1234567
        assert (
            extract_media_id("Some User 2024-01-01_at_22-15_UTC_id_9876543.mp4")
            == 9876543
        )
        assert (
            extract_media_id("2023-04-15_at_09-30_UTC_preview_id_1234567.jpg")
            == 1234567
        )


class TestImageHashIntegration:
    """Integration tests for the image hashing functions."""

    def test_get_hash_for_image_with_real_file(self, valid_image_file):
        """Test get_hash_for_image with a real image file."""
        # Get the hash
        hash_result = get_hash_for_image(valid_image_file)

        # Verify the result is a valid perceptual hash
        assert isinstance(hash_result, str)
        assert len(hash_result) > 0

        # Calculate the hash directly and compare
        with Image.open(valid_image_file) as img:
            direct_hash = str(imagehash.phash(img, hash_size=16))

        assert hash_result == direct_hash

    def test_get_hash_for_image_consistency(self, valid_image_file):
        """Test that get_hash_for_image returns consistent results."""
        # Get the hash twice
        hash1 = get_hash_for_image(valid_image_file)
        hash2 = get_hash_for_image(valid_image_file)

        # Verify the results are the same
        assert hash1 == hash2

    def test_get_hash_for_image_with_invalid_file(self, invalid_image_file):
        """Test get_hash_for_image with an invalid image file."""
        # Try to get the hash of an invalid image
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_image(invalid_image_file)

        # Verify the error message
        assert "Failed to verify image" in str(excinfo.value)

    def test_get_hash_for_image_with_nonexistent_file(self, tmp_path):
        """Test get_hash_for_image with a non-existent file."""
        # Try to get the hash of a non-existent file
        nonexistent_file = tmp_path / "nonexistent.jpg"
        with pytest.raises(Exception):
            get_hash_for_image(nonexistent_file)


class TestVideoHashIntegration:
    """Integration tests for the video hashing functions."""

    def test_get_hash_for_other_content_with_real_file(self, valid_mp4_file):
        """Test get_hash_for_other_content with a real MP4 file."""
        # Get the hash
        hash_result = get_hash_for_other_content(valid_mp4_file)

        # Verify the result is a valid hash string
        assert isinstance(hash_result, str)
        assert len(hash_result) > 0

        # Verify the hash is an MD5 hash (32 hex characters)
        assert len(hash_result) == 32
        # Check that it only contains hex characters
        assert all(c in "0123456789abcdef" for c in hash_result.lower())

    def test_get_hash_for_other_content_consistency(self, valid_mp4_file):
        """Test that get_hash_for_other_content returns consistent results."""
        # Get the hash twice
        hash1 = get_hash_for_other_content(valid_mp4_file)
        hash2 = get_hash_for_other_content(valid_mp4_file)

        # Verify the results are the same
        assert hash1 == hash2

    def test_get_hash_for_other_content_with_invalid_file(self, invalid_mp4_file):
        """Test get_hash_for_other_content with an invalid MP4 file."""
        # Try to get the hash of an invalid MP4
        with pytest.raises(InvalidMP4Error) as excinfo:
            get_hash_for_other_content(invalid_mp4_file)

        # In the production code, the error message explains why it's invalid
        assert "File header missing" in str(excinfo.value)
        assert "not an MPEG-4 file" in str(excinfo.value)

    def test_get_hash_for_other_content_with_nonexistent_file(self, tmp_path):
        """Test get_hash_for_other_content with a non-existent file."""
        # Try to get the hash of a non-existent file
        nonexistent_file = tmp_path / "nonexistent.mp4"
        with pytest.raises(RuntimeError):
            get_hash_for_other_content(nonexistent_file)
