"""Integration tests for the fnmanip module."""

import shutil
import tempfile
from pathlib import Path

import imagehash
import pytest
from PIL import Image

from errors.mp4 import InvalidMP4Error
from fileio.fnmanip import (
    extract_hash_from_filename,
    extract_media_id,
    extract_old_hash0_from_filename,
    extract_old_hash1_from_filename,
    get_hash_for_image,
    get_hash_for_other_content,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def valid_image_file(temp_dir):
    """Create a valid image file for testing."""
    file_path = temp_dir / "test_image.jpg"

    # Create a simple 10x10 red image
    image = Image.new("RGB", (10, 10), color="red")
    image.save(file_path)

    return file_path


@pytest.fixture
def valid_mp4_file(temp_dir):
    """Create a valid minimal MP4 file for testing."""
    file_path = temp_dir / "valid.mp4"

    # Create a minimal valid MP4 file
    with file_path.open("wb") as f:
        # ftyp box (24 bytes)
        f.write(bytes.fromhex("00000018 66747970 6D703432 00000000 6D703432 00000000"))
        # free box (16 bytes)
        f.write(bytes.fromhex("00000010 66726565 00000000 00000000"))
        # mdat box (16 bytes)
        f.write(bytes.fromhex("00000010 6D646174 00000000 00000000"))

    return file_path


@pytest.fixture
def invalid_image_file(temp_dir):
    """Create an invalid image file for testing."""
    file_path = temp_dir / "invalid_image.jpg"

    # Create a file that's not a valid image
    with file_path.open("wb") as f:
        f.write(b"This is not a valid image file")

    return file_path


@pytest.fixture
def invalid_mp4_file(temp_dir):
    """Create an invalid MP4 file for testing."""
    file_path = temp_dir / "invalid.mp4"

    # Create an invalid MP4 file (missing ftyp box)
    with file_path.open("wb") as f:
        # moov box (16 bytes)
        f.write(bytes.fromhex("00000010 6D6F6F76 00000000 00000000"))

    return file_path


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

    def test_hash_extractors_with_real_filenames(self):
        """Test hash extractors with some typical real-world filenames."""
        # Create test filenames
        filename1 = "2023-04-15_at_09-30_UTC_hash_abcdef123456.jpg"
        filename2 = "2023-04-15_at_09-30_UTC_hash1_abcdef123456.jpg"
        filename3 = "2023-04-15_at_09-30_UTC_hash2_abcdef123456.jpg"

        # Test hash extractors
        assert extract_old_hash0_from_filename(filename1) == "abcdef123456"
        assert extract_old_hash0_from_filename(filename2) is None
        assert extract_old_hash0_from_filename(filename3) is None

        assert extract_old_hash1_from_filename(filename1) is None
        assert extract_old_hash1_from_filename(filename2) == "abcdef123456"
        assert extract_old_hash1_from_filename(filename3) is None

        assert extract_hash_from_filename(filename1) is None
        assert extract_hash_from_filename(filename2) is None
        assert extract_hash_from_filename(filename3) == "abcdef123456"


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

    def test_get_hash_for_image_with_nonexistent_file(self, temp_dir):
        """Test get_hash_for_image with a non-existent file."""
        # Try to get the hash of a non-existent file
        nonexistent_file = temp_dir / "nonexistent.jpg"
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

    def test_get_hash_for_other_content_with_nonexistent_file(self, temp_dir):
        """Test get_hash_for_other_content with a non-existent file."""
        # Try to get the hash of a non-existent file
        nonexistent_file = temp_dir / "nonexistent.mp4"
        with pytest.raises(RuntimeError):
            get_hash_for_other_content(nonexistent_file)
