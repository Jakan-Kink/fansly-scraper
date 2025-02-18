"""File Name Manipulation Functions"""

import hashlib
import re
from pathlib import Path

import imagehash
from PIL import Image

from errors.mp4 import InvalidMP4Error
from fileio.mp4 import hash_mp4file

# turn off for our purpose unnecessary PIL safety features
Image.MAX_IMAGE_PIXELS = None


def extract_media_id(filename: str) -> int | None:
    """Extracts the media_id from an existing file's name."""
    match = re.search(r"_id_(\d+)", filename)

    if match:
        return int(match.group(1))

    return None


def extract_old_hash0_from_filename(filename: str) -> str | None:
    """Extracts hash v0 from an existing file's name."""
    match = re.search(r"_hash_([a-fA-F0-9]+)", filename)

    if match:
        return match.group(1)

    return None


def extract_old_hash1_from_filename(filename: str) -> str | None:
    """Extracts hash v1 from an existing file's name."""
    match = re.search(r"_hash1_([a-fA-F0-9]+)", filename)

    if match:
        return match.group(1)

    return None


def extract_hash_from_filename(filename: str) -> str | None:
    """Extracts the hash from an existing file's name."""
    match = re.search(r"_hash2_([a-fA-F0-9]+)", filename)

    if match:
        return match.group(1)

    return None


def get_hash_for_image(filename: Path) -> str:
    """Get hash for an image file.

    Args:
        filename: Path to image file

    Returns:
        Hash string

    Raises:
        RuntimeError: If hash could not be generated
        OSError: If file could not be opened
    """
    # First verify the file is valid
    try:
        with Image.open(filename) as verify_img:
            verify_img.verify()
    except Exception as e:
        raise RuntimeError(f"Failed to verify image {filename}: {e}")

    # Then open again for hashing (verify closes the file)
    try:
        with Image.open(filename) as img:
            file_hash = str(imagehash.phash(img, hash_size=16))
            if file_hash is None:
                raise RuntimeError("Hash generation returned None")
            return file_hash
    except Exception as e:
        raise RuntimeError(f"Failed to hash image {filename}: {e}")


def get_hash_for_other_content(filename: Path) -> str:
    """Get hash for a non-image file (video/audio).

    Args:
        filename: Path to file

    Returns:
        Hash string

    Raises:
        InvalidMP4Error: If file is not a valid MP4
        OSError: If file could not be opened
    """
    algorithm = hashlib.md5(usedforsecurity=False)
    try:
        file_hash = hash_mp4file(algorithm, filename)
        if not file_hash:
            raise RuntimeError("Failed to generate hash")
        return file_hash
    except Exception as e:
        if isinstance(e, InvalidMP4Error):
            raise
        raise RuntimeError(f"Failed to hash file {filename}: {e}")
