"""Unit tests for the MP4 module."""

from io import BufferedReader, BytesIO
from pathlib import Path
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
        # Create a mock MP4 file
        mock_data = BytesIO(test_data)
        mock_data.seek(0)
        reader = BufferedReader(mock_data)

        # Create a mock box with size matching test data
        box = MP4Box(
            size_bytes=len(test_data).to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )

        # Create a mock hash algorithm
        mock_algorithm = MagicMock()

        # Call hash_mp4box
        hash_mp4box(mock_algorithm, reader, box)

        # Verify algorithm.update was called with the data
        mock_algorithm.update.assert_called_with(test_data)

    def test_hash_mp4box_large(self):
        """Test hashing a large MP4 box that requires multiple chunks."""
        # Create a mock box with size larger than the chunk size
        box_size = 2_000_000  # 2MB, larger than the 1MB chunk size

        # Create a mock MP4 file with box_size bytes of data
        mock_data = BytesIO(b"x" * box_size)
        mock_data.seek(0)
        reader = BufferedReader(mock_data)

        # Create a mock box
        box = MP4Box(
            size_bytes=box_size.to_bytes(4, byteorder="big"),
            fourcc_bytes=b"ftyp",
            position=0,
        )

        # Create a mock hash algorithm
        mock_algorithm = MagicMock()

        # Call hash_mp4box
        hash_mp4box(mock_algorithm, reader, box)

        # Verify algorithm.update was called twice (once for each chunk)
        assert mock_algorithm.update.call_count == 2
        # First chunk should be 1MB
        mock_algorithm.update.assert_any_call(b"x" * 1_048_576)
        # Second chunk should be the remainder
        mock_algorithm.update.assert_any_call(b"x" * (box_size - 1_048_576))


class TestHashMP4File:
    """Test the hash_mp4file function."""

    @patch("fileio.mp4.get_boxes")
    @patch("fileio.mp4.hash_mp4box")
    def test_hash_mp4file(self, mock_hash_mp4box, mock_get_boxes):
        """Test hashing an MP4 file."""
        # Create mock boxes
        ftyp_box = MagicMock()
        ftyp_box.fourcc = "ftyp"

        moov_box = MagicMock()
        moov_box.fourcc = "moov"

        mdat_box = MagicMock()
        mdat_box.fourcc = "mdat"

        free_box = MagicMock()
        free_box.fourcc = "free"

        # Set up mock_get_boxes to return our mock boxes
        mock_get_boxes.return_value = [ftyp_box, moov_box, mdat_box, free_box]

        # Mock the file operations
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value = MagicMock(st_size=1000)

        # Mock open to return a file-like object
        mock_file = MagicMock(spec=BufferedReader)
        mock_open = MagicMock()
        mock_open.__enter__ = MagicMock(return_value=mock_file)
        mock_open.__exit__ = MagicMock(return_value=False)

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()
        mock_algorithm.hexdigest.return_value = "test_hash"

        with patch("builtins.open", return_value=mock_open):
            # Call hash_mp4file without broken algorithm flag
            result = hash_mp4file(mock_algorithm, mock_path)

            # Verify result
            assert result == "test_hash"

            # Verify get_boxes was called with the context manager's file
            mock_get_boxes.assert_called_once_with(mock_file)

            # Verify hash_mp4box was called for ftyp and mdat but not moov or free
            assert mock_hash_mp4box.call_count == 2
            mock_hash_mp4box.assert_any_call(mock_algorithm, mock_file, ftyp_box)
            mock_hash_mp4box.assert_any_call(mock_algorithm, mock_file, mdat_box)

    @patch("fileio.mp4.get_boxes")
    @patch("fileio.mp4.hash_mp4box")
    def test_hash_mp4file_with_broken_algo(self, mock_hash_mp4box, mock_get_boxes):
        """Test hashing an MP4 file with broken algorithm flag."""
        # Create mock boxes
        ftyp_box = MagicMock()
        ftyp_box.fourcc = "ftyp"

        moov_box = MagicMock()
        moov_box.fourcc = "moov"

        mdat_box = MagicMock()
        mdat_box.fourcc = "mdat"

        free_box = MagicMock()
        free_box.fourcc = "free"

        # Set up mock_get_boxes to return our mock boxes
        mock_get_boxes.return_value = [ftyp_box, moov_box, mdat_box, free_box]

        # Mock the file operations
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value = MagicMock(st_size=1000)

        # Mock open to return a file-like object
        mock_file = MagicMock(spec=BufferedReader)
        mock_open = MagicMock()
        mock_open.__enter__ = MagicMock(return_value=mock_file)
        mock_open.__exit__ = MagicMock(return_value=False)

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()
        mock_algorithm.hexdigest.return_value = "test_hash"

        with patch("builtins.open", return_value=mock_open):
            # Call hash_mp4file with broken algorithm flag
            result = hash_mp4file(mock_algorithm, mock_path, use_broken_algo=True)

            # Verify result
            assert result == "test_hash"

            # Verify get_boxes was called with the context manager's file
            mock_get_boxes.assert_called_once_with(mock_file)

            # Verify hash_mp4box was called for ftyp and free but not moov or mdat
            assert mock_hash_mp4box.call_count == 2
            mock_hash_mp4box.assert_any_call(mock_algorithm, mock_file, ftyp_box)
            mock_hash_mp4box.assert_any_call(mock_algorithm, mock_file, free_box)

    def test_hash_mp4file_missing_file(self):
        """Test hashing a non-existent file."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()

        with pytest.raises(RuntimeError):
            hash_mp4file(mock_algorithm, mock_path)

    def test_hash_mp4file_too_small(self):
        """Test hashing a file that's too small to be an MP4."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value = MagicMock(st_size=7)  # Less than 8 bytes

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()

        with pytest.raises(InvalidMP4Error):
            hash_mp4file(mock_algorithm, mock_path)

    @patch("fileio.mp4.get_boxes")
    def test_hash_mp4file_with_print(self, mock_get_boxes):
        """Test hashing an MP4 file with print function."""
        # Create mock boxes
        ftyp_box = MagicMock()
        ftyp_box.fourcc = "ftyp"
        mock_get_boxes.return_value = [ftyp_box]

        # Mock the file operations
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value = MagicMock(st_size=1000)

        # Mock open to return a file-like object
        mock_file = MagicMock(spec=BufferedReader)
        mock_open = MagicMock(return_value=mock_file)
        mock_open.__enter__ = MagicMock(return_value=mock_file)
        mock_open.__exit__ = MagicMock(return_value=False)

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()
        mock_algorithm.hexdigest.return_value = "test_hash"

        # Mock print function
        mock_print = MagicMock()

        with patch("builtins.open", mock_open):
            # Call hash_mp4file with print function
            result = hash_mp4file(mock_algorithm, mock_path, print=mock_print)

            # Verify result
            assert result == "test_hash"

            # Verify print was called
            assert mock_print.call_count >= 3  # At least 3 calls (file, box, hash)

    @patch("fileio.mp4.get_boxes")
    def test_hash_mp4file_invalid_mp4_error(self, mock_get_boxes):
        """Test handling InvalidMP4Error during hash_mp4file."""
        # Mock get_boxes to raise InvalidMP4Error
        mock_get_boxes.side_effect = InvalidMP4Error("Test error")

        # Mock the file operations
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value = MagicMock(st_size=1000)

        # Mock file object
        mock_file = MagicMock(spec=BufferedReader)
        mock_open = MagicMock()
        mock_open.__enter__ = MagicMock(return_value=mock_file)
        mock_open.__exit__ = MagicMock(return_value=False)

        # Mock hashlib algorithm
        mock_algorithm = MagicMock()

        with patch("builtins.open", return_value=mock_open):
            with pytest.raises(InvalidMP4Error) as excinfo:
                hash_mp4file(mock_algorithm, mock_path)

            # Verify the error message includes the file name
            assert str(mock_path) in str(excinfo.value)
