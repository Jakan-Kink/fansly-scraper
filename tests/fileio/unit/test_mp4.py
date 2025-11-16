"""Unit tests for the MP4 module."""

import hashlib
from io import BufferedReader, BytesIO
from unittest.mock import MagicMock, patch

import pytest

from errors.mp4 import InvalidMP4Error
from fileio.mp4 import MP4Box, get_boxes, hash_mp4box, hash_mp4file


class TestMP4Box:
    """Test the MP4Box class."""

    def test_init(self):
        """Test initialization of MP4Box."""
        # Test with valid inputs
        size_bytes = (1234).to_bytes(4, byteorder="big")
        fourcc_bytes = b"ftyp"
        position = 0

        box = MP4Box(size_bytes, fourcc_bytes, position)

        assert box.position == 0
        assert box.size == 1234
        assert box.fourcc == "ftyp"

    def test_str(self):
        """Test the string representation of MP4Box."""
        size_bytes = (1234).to_bytes(4, byteorder="big")
        fourcc_bytes = b"ftyp"
        position = 0

        box = MP4Box(size_bytes, fourcc_bytes, position)
        expected = "MP4Box ( Position: 0, FourCC: ftyp, Size: 1234 )"

        assert str(box) == expected

    def test_convert_to_fourcc_ascii(self):
        """Test conversion of ASCII bytes to FourCC."""
        fourcc_bytes = b"moov"
        result = MP4Box.convert_to_fourcc(fourcc_bytes)
        assert result == "moov"

    def test_convert_to_fourcc_non_ascii(self):
        """Test conversion of non-ASCII bytes to FourCC."""
        # Non-printable ASCII bytes should be preserved as-is
        fourcc_bytes = bytes([0x00, 0x41, 0x03, 0x42])  # [NUL, 'A', ETX, 'B']
        result = MP4Box.convert_to_fourcc(fourcc_bytes)
        assert result == "\x00A\x03B"  # Raw bytes preserved


class TestGetBoxes:
    """Test the get_boxes function."""

    def test_valid_mp4(self):
        """Test get_boxes with valid MP4 data."""
        # Create a mock MP4 file with two boxes: ftyp and moov
        mock_data = BytesIO()
        # ftyp box: size=16 (4 bytes), fourcc="ftyp" (4 bytes), data (8 bytes)
        mock_data.write((16).to_bytes(4, byteorder="big"))
        mock_data.write(b"ftyp")
        mock_data.write(b"mp42\x00\x00\x00\x00")
        # moov box: size=16 (4 bytes), fourcc="moov" (4 bytes), data (8 bytes)
        mock_data.write((16).to_bytes(4, byteorder="big"))
        mock_data.write(b"moov")
        mock_data.write(b"\x00\x00\x00\x00\x00\x00\x00\x00")
        mock_data.seek(0)

        # Convert BytesIO to BufferedReader
        reader = BufferedReader(mock_data)

        # Call get_boxes and collect results
        boxes = list(get_boxes(reader))

        # Verify the boxes
        assert len(boxes) == 2

        assert boxes[0].fourcc == "ftyp"
        assert boxes[0].size == 16
        assert boxes[0].position == 0

        assert boxes[1].fourcc == "moov"
        assert boxes[1].size == 16
        assert boxes[1].position == 16

    def test_invalid_mp4(self):
        """Test get_boxes with invalid MP4 data (missing ftyp box)."""
        # Create a mock MP4 file with one invalid box (not starting with ftyp)
        mock_data = BytesIO()
        mock_data.write((16).to_bytes(4, byteorder="big"))
        mock_data.write(b"moov")  # Should be ftyp for a valid MP4
        mock_data.write(b"\x00\x00\x00\x00\x00\x00\x00\x00")
        mock_data.seek(0)

        # Convert BytesIO to BufferedReader
        reader = BufferedReader(mock_data)

        # Call get_boxes and expect an exception
        with pytest.raises(InvalidMP4Error):
            list(get_boxes(reader))

    def test_wide_box_size(self):
        """Test get_boxes with a wide box size (size=1 followed by 8-byte size)."""
        # Create a mock MP4 file with ftyp box followed by a wide box
        mock_data = BytesIO()
        # ftyp box: size=24, fourcc="ftyp", data (16 bytes)
        mock_data.write((24).to_bytes(4, byteorder="big"))  # size
        mock_data.write(b"ftyp")  # fourcc
        mock_data.write(b"mp42\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")  # data

        # Wide box: initial size=1 (4 bytes), fourcc="wide" (4 bytes)
        mock_data.write((1).to_bytes(4, byteorder="big"))  # size=1 signals wide box
        mock_data.write(b"wide")  # fourcc
        # Actual 64-bit size = 32 (8 bytes)
        mock_data.write((32).to_bytes(8, byteorder="big"))
        # Box data (32 - 16 = 16 bytes, accounting for header)
        mock_data.write(b"\x00" * 16)
        mock_data.seek(0)

        # Convert BytesIO to BufferedReader
        reader = BufferedReader(mock_data)

        # Call get_boxes and collect results
        boxes = list(get_boxes(reader))

        # Verify the boxes
        assert len(boxes) == 2

        # First box should be normal ftyp
        assert boxes[0].fourcc == "ftyp"
        assert boxes[0].size == 24
        assert boxes[0].position == 0

        # Second box should be wide box
        assert boxes[1].fourcc == "wide"
        assert boxes[1].size == 32  # This is the actual total size including header
        assert boxes[1].position == 24  # Position starts after first box


class TestHashMP4Box:
    """Test the hash_mp4box function."""

    def test_hash_mp4box(self):
        """Test hashing an MP4 box."""
        test_data = b"test data for hashing"
        # Create test data stream
        mock_data = BytesIO(test_data)
        mock_data.seek(0)
        reader = BufferedReader(mock_data)

        # Create a real box with size matching test data
        box = MP4Box(
            size_bytes=len(test_data).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        # Call hash_mp4box
        hash_mp4box(algorithm, reader, box)

        # Verify the hash was computed correctly by hashing the same data
        expected_algorithm = hashlib.md5(usedforsecurity=False)
        expected_algorithm.update(test_data)
        assert algorithm.digest() == expected_algorithm.digest()

    def test_hash_mp4box_large(self):
        """Test hashing a large MP4 box that requires multiple chunks."""
        # Create a box with size larger than the chunk size
        box_size = 2_000_000  # 2MB, larger than the 1MB chunk size
        test_data = b"x" * box_size

        # Create test data stream
        mock_data = BytesIO(test_data)
        mock_data.seek(0)
        reader = BufferedReader(mock_data)

        # Create a real box
        box = MP4Box(
            size_bytes=box_size.to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        # Call hash_mp4box
        hash_mp4box(algorithm, reader, box)

        # Verify the hash matches what we'd get from hashing the entire data
        expected_algorithm = hashlib.md5(usedforsecurity=False)
        expected_algorithm.update(test_data)
        assert algorithm.digest() == expected_algorithm.digest()


class TestHashMP4File:
    """Test the hash_mp4file function."""

    @patch("fileio.mp4.get_boxes")
    @patch("fileio.mp4.hash_mp4box")
    def test_hash_mp4file(self, mock_hash_mp4box, mock_get_boxes, tmp_path):
        """Test hashing an MP4 file."""
        # Create real box objects (not mocks)
        ftyp_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )
        moov_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"moov",
            position=16,
        )
        mdat_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"mdat",
            position=32,
        )
        free_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"free",
            position=48,
        )

        # Set up mock_get_boxes to return our real box objects
        mock_get_boxes.return_value = [ftyp_box, moov_box, mdat_box, free_box]

        # Create a real test file with sufficient size
        test_file = tmp_path / "test_file.mp4"
        test_file.write_bytes(b"\x00" * 1000)  # 1000 bytes

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        # Call hash_mp4file without broken algorithm flag
        result = hash_mp4file(algorithm, test_file)

        # Verify result is a valid MD5 hash (32 hex characters)
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

        # Verify hash_mp4box was called for ftyp and mdat but not moov or free
        assert mock_hash_mp4box.call_count == 2
        # Get the actual calls to verify box types
        call_boxes = [call[0][2] for call in mock_hash_mp4box.call_args_list]
        assert ftyp_box in call_boxes
        assert mdat_box in call_boxes

    @patch("fileio.mp4.get_boxes")
    @patch("fileio.mp4.hash_mp4box")
    def test_hash_mp4file_with_broken_algo(
        self, mock_hash_mp4box, mock_get_boxes, tmp_path
    ):
        """Test hashing an MP4 file with broken algorithm flag."""
        # Create real box objects (not mocks)
        ftyp_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )
        moov_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"moov",
            position=16,
        )
        mdat_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"mdat",
            position=32,
        )
        free_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"free",
            position=48,
        )

        # Set up mock_get_boxes to return our real box objects
        mock_get_boxes.return_value = [ftyp_box, moov_box, mdat_box, free_box]

        # Create a real test file with sufficient size
        test_file = tmp_path / "test_file.mp4"
        test_file.write_bytes(b"\x00" * 1000)  # 1000 bytes

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        # Call hash_mp4file with broken algorithm flag
        result = hash_mp4file(algorithm, test_file, use_broken_algo=True)

        # Verify result is a valid MD5 hash (32 hex characters)
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

        # Verify hash_mp4box was called for ftyp and free but not moov or mdat
        assert mock_hash_mp4box.call_count == 2
        # Get the actual calls to verify box types
        call_boxes = [call[0][2] for call in mock_hash_mp4box.call_args_list]
        assert ftyp_box in call_boxes
        assert free_box in call_boxes

    def test_hash_mp4file_missing_file(self, tmp_path):
        """Test hashing a non-existent file."""
        # Use a real path that doesn't exist
        non_existent_file = tmp_path / "does_not_exist.mp4"

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        with pytest.raises(RuntimeError):
            hash_mp4file(algorithm, non_existent_file)

    def test_hash_mp4file_too_small(self, tmp_path):
        """Test hashing a file that's too small to be an MP4."""
        # Create a real file that's too small
        small_file = tmp_path / "too_small.mp4"
        small_file.write_bytes(b"\x00" * 7)  # Less than 8 bytes

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        with pytest.raises(InvalidMP4Error):
            hash_mp4file(algorithm, small_file)

    @patch("fileio.mp4.get_boxes")
    def test_hash_mp4file_with_print(self, mock_get_boxes, tmp_path):
        """Test hashing an MP4 file with print function."""
        # Create a real box object
        ftyp_box = MP4Box(
            size_bytes=(16).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )
        mock_get_boxes.return_value = [ftyp_box]

        # Create a real test file
        test_file = tmp_path / "test_file.mp4"
        test_file.write_bytes(b"\x00" * 1000)  # 1000 bytes

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        # Mock print function to verify it's called (testing output behavior)
        mock_print = MagicMock()

        # Call hash_mp4file with print function
        result = hash_mp4file(algorithm, test_file, print=mock_print)

        # Verify result is a valid MD5 hash
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

        # Verify print was called (at least for file and hash output)
        assert mock_print.call_count >= 2

    @patch("fileio.mp4.get_boxes")
    def test_hash_mp4file_invalid_mp4_error(self, mock_get_boxes, tmp_path):
        """Test handling InvalidMP4Error during hash_mp4file."""
        # Mock get_boxes to raise InvalidMP4Error (simulating rare edge case)
        mock_get_boxes.side_effect = InvalidMP4Error("Test error")

        # Create a real test file
        test_file = tmp_path / "test_file.mp4"
        test_file.write_bytes(b"\x00" * 1000)  # 1000 bytes

        # Use real hash algorithm
        algorithm = hashlib.md5(usedforsecurity=False)

        with pytest.raises(InvalidMP4Error) as excinfo:
            hash_mp4file(algorithm, test_file)

        # Verify the error message includes the file name
        assert str(test_file) in str(excinfo.value)
