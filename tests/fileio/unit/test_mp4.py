"""Unit tests for the MP4 module."""

import hashlib
from io import BufferedReader, BytesIO
from typing import Any

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
        """Test conversion of non-ASCII bytes to FourCC (UnicodeDecodeError path)."""
        # Bytes > 127 cannot be decoded as ASCII, triggering UnicodeDecodeError
        # This tests lines 44-49: the exception handler that formats non-ASCII bytes
        fourcc_bytes = bytes([0xFF, 0x41, 0x80, 0x42])  # Non-ASCII bytes 0xFF and 0x80

        result = MP4Box.convert_to_fourcc(fourcc_bytes)

        # Bytes < 32 or > 126 are wrapped in [brackets], printable ASCII chars pass through
        # 0xFF (255) -> "[255]"
        # 0x41 (65 = 'A') -> "A"
        # 0x80 (128) -> "[128]"
        # 0x42 (66 = 'B') -> "B"
        assert result == "[255]A[128]B"


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
        reader: BufferedReader[Any] = BufferedReader(mock_data)

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
        reader: BufferedReader[Any] = BufferedReader(mock_data)

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
        reader: BufferedReader[Any] = BufferedReader(mock_data)

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
        reader: BufferedReader[Any] = BufferedReader(mock_data)

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
        reader: BufferedReader[Any] = BufferedReader(mock_data)

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

    @pytest.mark.parametrize(
        ("use_broken_algo", "hashed_boxes"),
        [
            # Normal algo hashes every box except {free, moov}: ftyp + mdat.
            (False, ("ftyp", "mdat")),
            # Broken algo hashes every box except {moov, mdat}: ftyp + free.
            (True, ("ftyp", "free")),
        ],
    )
    def test_hash_mp4file_box_selection(
        self, valid_mp4_file, use_broken_algo, hashed_boxes
    ):
        """Test hash_mp4file end-to-end over a real MP4, asserting box selection.

        Previously this was two shallow tests that stubbed the internal
        ``get_boxes`` + ``hash_mp4box`` collaborators and asserted on spy
        call-counts, so the real box-parse + box-hash pipeline never ran. Here a
        real MP4 (ftyp + free + mdat boxes) is parsed by the real ``get_boxes``
        and hashed by the real ``hash_mp4box``; we independently recompute the
        MD5 over exactly the boxes the algorithm should select and assert the
        digests match -- proving which box bytes fed the digest.
        """
        raw = valid_mp4_file.read_bytes()
        # Box layout for valid_mp4_file: ftyp (24 bytes), free (16), mdat (16).
        boxes = {
            "ftyp": raw[0:24],
            "free": raw[24:40],
            "mdat": raw[40:56],
        }

        expected = hashlib.md5(usedforsecurity=False)
        for fourcc in hashed_boxes:
            expected.update(boxes[fourcc])

        algorithm = hashlib.md5(usedforsecurity=False)
        result = hash_mp4file(
            algorithm, valid_mp4_file, use_broken_algo=use_broken_algo
        )

        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)
        assert result == expected.hexdigest()

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

    def test_hash_mp4file_with_print(self, valid_mp4_file):
        """Test hashing a real MP4 file with a print function.

        Previously stubbed the internal ``get_boxes`` collaborator; now a real
        MP4 is parsed and hashed end-to-end. ``print`` is a real callable whose
        output we capture to verify the diagnostic output behavior.
        """
        printed: list[str] = []

        algorithm = hashlib.md5(usedforsecurity=False)
        result = hash_mp4file(
            algorithm,
            valid_mp4_file,
            print=lambda *a: printed.append(" ".join(map(str, a))),
        )

        # Verify result is a valid MD5 hash
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

        # print receives the file line, each box, and the final hash line.
        assert any(str(valid_mp4_file) in line for line in printed)
        assert any("Hash:" in line for line in printed)

    def test_hash_mp4file_invalid_mp4_error(self, invalid_mp4_file):
        """Test InvalidMP4Error from a real malformed MP4 (first box not ftyp).

        Previously stubbed ``get_boxes`` with a side_effect to fake the error;
        now the real ``get_boxes`` raises ``InvalidMP4Error`` when it reads a
        first box whose fourcc is not ``ftyp``, and ``hash_mp4file`` re-raises
        it wrapped with the file name.
        """
        algorithm = hashlib.md5(usedforsecurity=False)

        with pytest.raises(InvalidMP4Error) as excinfo:
            hash_mp4file(algorithm, invalid_mp4_file)

        # Verify the error message includes the file name
        assert str(invalid_mp4_file) in str(excinfo.value)
