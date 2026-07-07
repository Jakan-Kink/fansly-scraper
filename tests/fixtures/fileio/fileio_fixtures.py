"""File-content fixtures: minimal real MP4 and image files for fileio tests."""

import io

import pytest
from PIL import Image


def tiny_jpeg_bytes() -> bytes:
    """Return a minimal valid JPEG as bytes for image-hash / CDN-serve tests."""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def valid_mp4_file(tmp_path):
    """Create a valid minimal MP4 file for testing."""
    file_path = tmp_path / "valid.mp4"

    with file_path.open("wb") as f:
        # ftyp box (24 bytes)
        f.write(bytes.fromhex("00000018 66747970 6D703432 00000000 6D703432 00000000"))
        # free box (16 bytes)
        f.write(bytes.fromhex("00000010 66726565 00000000 00000000"))
        # mdat box (16 bytes)
        f.write(bytes.fromhex("00000010 6D646174 00000000 00000000"))

    return file_path


@pytest.fixture
def invalid_mp4_file(tmp_path):
    """Create an invalid MP4 file (missing ftyp box) for testing."""
    file_path = tmp_path / "invalid.mp4"

    with file_path.open("wb") as f:
        # moov box (16 bytes)
        f.write(bytes.fromhex("00000010 6D6F6F76 00000000 00000000"))

    return file_path


@pytest.fixture
def too_small_mp4_file(tmp_path):
    """Create a file that's too small to be an MP4."""
    file_path = tmp_path / "too_small.mp4"

    with file_path.open("wb") as f:
        f.write(bytes.fromhex("0000"))  # Only 2 bytes

    return file_path


@pytest.fixture
def valid_image_file(tmp_path):
    """Create a valid image file (10x10 red JPEG) for testing."""
    file_path = tmp_path / "test_image.jpg"

    image = Image.new("RGB", (10, 10), color="red")
    image.save(file_path)

    return file_path


@pytest.fixture
def invalid_image_file(tmp_path):
    """Create a file that is not a valid image."""
    file_path = tmp_path / "invalid_image.jpg"

    with file_path.open("wb") as f:
        f.write(b"This is not a valid image file")

    return file_path


__all__ = [
    "invalid_image_file",
    "invalid_mp4_file",
    "tiny_jpeg_bytes",
    "too_small_mp4_file",
    "valid_image_file",
    "valid_mp4_file",
]
