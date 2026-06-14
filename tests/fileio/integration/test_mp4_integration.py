"""Integration tests for the MP4 module."""

import hashlib

import pytest

from errors.mp4 import InvalidMP4Error
from fileio.mp4 import get_boxes, hash_mp4file


class TestMP4Integration:
    """Integration tests for the MP4 module."""

    def test_get_boxes_with_real_file(self, valid_mp4_file):
        """Test get_boxes with a real MP4 file."""
        with valid_mp4_file.open("rb") as f:
            boxes = list(get_boxes(f))

        # Validate box count and types
        assert len(boxes) == 3
        assert [box.fourcc for box in boxes] == ["ftyp", "free", "mdat"]
        assert [box.position for box in boxes] == [0, 24, 40]
        assert [box.size for box in boxes] == [24, 16, 16]

    def test_get_boxes_with_invalid_file(self, invalid_mp4_file):
        """Test get_boxes with an invalid MP4 file."""
        with invalid_mp4_file.open("rb") as f, pytest.raises(InvalidMP4Error):
            list(get_boxes(f))

    def test_hash_mp4file_with_real_file(self, valid_mp4_file):
        """Test hash_mp4file with a real MP4 file."""
        algorithm = hashlib.md5(usedforsecurity=False)
        result = hash_mp4file(algorithm, valid_mp4_file)

        # The hash should be consistent for the same file
        algorithm2 = hashlib.md5(usedforsecurity=False)
        result2 = hash_mp4file(algorithm2, valid_mp4_file)

        assert result == result2
        assert len(result) == 32  # MD5 hash is 32 hex characters

    def test_hash_mp4file_different_hash_algorithms(self, valid_mp4_file):
        """Test hash_mp4file with different hash algorithms."""
        # Test with MD5
        md5_algorithm = hashlib.md5(usedforsecurity=False)
        md5_result = hash_mp4file(md5_algorithm, valid_mp4_file)

        # Test with SHA1
        sha1_algorithm = hashlib.sha1(usedforsecurity=False)
        sha1_result = hash_mp4file(sha1_algorithm, valid_mp4_file)

        # Test with SHA256
        sha256_algorithm = hashlib.sha256(usedforsecurity=False)
        sha256_result = hash_mp4file(sha256_algorithm, valid_mp4_file)

        # Verify results have expected lengths
        assert len(md5_result) == 32  # MD5 is 32 hex characters
        assert len(sha1_result) == 40  # SHA1 is 40 hex characters
        assert len(sha256_result) == 64  # SHA256 is 64 hex characters

        # Verify results are different
        assert md5_result != sha1_result
        assert md5_result != sha256_result
        assert sha1_result != sha256_result

    def test_hash_mp4file_with_broken_algo_flag(self, valid_mp4_file):
        """Test hash_mp4file with broken algorithm flag."""
        # Hash with normal algorithm
        algorithm1 = hashlib.md5(usedforsecurity=False)
        result1 = hash_mp4file(algorithm1, valid_mp4_file)

        # Hash with broken algorithm flag
        algorithm2 = hashlib.md5(usedforsecurity=False)
        result2 = hash_mp4file(algorithm2, valid_mp4_file, use_broken_algo=True)

        # The results should be different
        assert result1 != result2

    def test_hash_mp4file_with_invalid_file(self, invalid_mp4_file):
        """Test hash_mp4file with an invalid MP4 file."""
        algorithm = hashlib.md5(usedforsecurity=False)
        with pytest.raises(InvalidMP4Error):
            hash_mp4file(algorithm, invalid_mp4_file)

    def test_hash_mp4file_with_too_small_file(self, too_small_mp4_file):
        """Test hash_mp4file with a file that's too small to be an MP4."""
        algorithm = hashlib.md5(usedforsecurity=False)
        with pytest.raises(InvalidMP4Error):
            hash_mp4file(algorithm, too_small_mp4_file)

    def test_hash_mp4file_with_nonexistent_file(self, tmp_path):
        """Test hash_mp4file with a non-existent file."""
        nonexistent_file = tmp_path / "nonexistent.mp4"
        algorithm = hashlib.md5(usedforsecurity=False)
        with pytest.raises(RuntimeError):
            hash_mp4file(algorithm, nonexistent_file)

    def test_hash_mp4file_with_print_function(self, valid_mp4_file, capsys):
        """Test hash_mp4file with print function."""
        algorithm = hashlib.md5(usedforsecurity=False)
        result = hash_mp4file(algorithm, valid_mp4_file, print=print)

        # Capture the output
        captured = capsys.readouterr()

        # Verify the output contains expected information
        assert str(valid_mp4_file) in captured.out
        assert "MP4Box" in captured.out
        assert "ftyp" in captured.out
        assert "Hash: " in captured.out
        assert result in captured.out
