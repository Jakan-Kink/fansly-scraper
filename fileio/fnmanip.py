"""File Name Manipulation Functions"""

import concurrent.futures
import hashlib
import mimetypes
import os
import re
import traceback
from pathlib import Path

import imagehash
from PIL import Image

from config import FanslyConfig
from download.downloadstate import DownloadState
from errors.mp4 import InvalidMP4Error
from textio import print_debug, print_error

from .mp4 import hash_mp4file

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
    file_hash = None

    with Image.open(filename) as img:
        file_hash = str(imagehash.phash(img, hash_size=16))

    if file_hash is None:
        raise RuntimeError('add_hash_to_image: file_hash should not be "None"')

    return file_hash


def get_hash_for_other_content(filename: Path) -> str:
    algorithm = hashlib.md5(usedforsecurity=False)
    file_hash = hash_mp4file(algorithm, filename)
    return file_hash


def add_hash_to_filename(filename: Path, file_hash: str) -> str:
    """Adds a hash to an existing file's name."""
    base_name, extension = str(filename.parent / filename.stem), filename.suffix
    # old_hash_suffix_0 = f"_hash_{file_hash}{extension}"
    # old_hash_suffix_1 = f"_hash1_{file_hash}{extension}"
    hash_suffix = f"_hash2_{file_hash}{extension}"

    # Remove old hashes
    if extract_old_hash0_from_filename(str(filename)) is not None:
        base_name = base_name.split("_hash_")[0]

    if extract_old_hash1_from_filename(str(filename)) is not None:
        base_name = base_name.split("_hash1_")[0]

    # adjust filename for 255 bytes filename limit, on all common operating systems
    max_length = 250

    if len(base_name) + len(hash_suffix) > max_length:
        base_name = base_name[: max_length - len(hash_suffix)]

    return f"{base_name}{hash_suffix}"


def add_hash_to_image(state: DownloadState, filepath: Path):
    """Hashes existing images in download directories."""
    try:
        filename = filepath.name

        media_id = extract_media_id(filename)

        if media_id:
            state.recent_photo_media_ids.add(media_id)

        existing_hash = extract_hash_from_filename(filename)

        if existing_hash:
            state.recent_photo_hashes.add(existing_hash)

        else:
            file_hash = get_hash_for_image(filepath)

            state.recent_photo_hashes.add(file_hash)

            new_filename = add_hash_to_filename(Path(filename), file_hash)
            new_filepath = filepath.parent / new_filename

            if new_filepath.exists():
                filepath.unlink()
            else:
                filepath.rename(new_filepath)

    except Exception:
        print_error(
            f"\nError processing image '{filepath}': {traceback.format_exc()}", 15
        )


def add_hash_to_other_content(
    state: DownloadState, filepath: Path, content_format: str
):
    """Hashes audio and video files in download directories."""

    try:
        filename = filepath.name

        media_id = extract_media_id(filename)

        if media_id:

            if content_format == "video":
                state.recent_video_media_ids.add(media_id)

            elif content_format == "audio":
                state.recent_audio_media_ids.add(media_id)

        existing_hash = extract_hash_from_filename(filename)

        if existing_hash:

            if content_format == "video":
                state.recent_video_hashes.add(existing_hash)

            elif content_format == "audio":
                state.recent_audio_hashes.add(existing_hash)

        else:
            file_hash = get_hash_for_other_content(filepath)

            if content_format == "video":
                state.recent_video_hashes.add(file_hash)

            elif content_format == "audio":
                state.recent_audio_hashes.add(file_hash)

            new_filename = add_hash_to_filename(Path(filename), file_hash)
            new_filepath = filepath.parent / new_filename

            if new_filepath.exists():
                filepath.unlink()
            else:
                filepath = filepath.rename(new_filepath)

    except InvalidMP4Error as ex:
        print_error(
            f"Invalid MPEG-4 file found on disk - maybe a broken download due to server or Internet issues."
            f"\n{' ' * 17} Delete it if it doesn't play in your favorite video player so it will be re-downloaded if still available."
            f"\n{' ' * 17} {ex}"
        )

    except Exception:
        print_error(
            f"\nError processing {content_format} '{filepath}': {traceback.format_exc()}",
            16,
        )


def add_hash_to_file(
    config: FanslyConfig, state: DownloadState, file_path: Path
) -> None:
    """Hashes a file according to it's file type."""

    mimetype, _ = mimetypes.guess_type(file_path)

    if config.debug:
        print_debug(f"Hashing file of type '{mimetype}' at location '{file_path}' ...")

    if mimetype is not None:

        if mimetype.startswith("image"):
            add_hash_to_image(state, file_path)

        elif mimetype.startswith("video"):
            add_hash_to_other_content(state, file_path, content_format="video")

        elif mimetype.startswith("audio"):
            add_hash_to_other_content(state, file_path, content_format="audio")


def add_hash_to_folder_items(config: FanslyConfig, state: DownloadState) -> None:
    """Recursively adds hashes to all media files in the folder and
    it's sub-folders.
    """

    if state.download_path is None:
        raise RuntimeError(
            "Internal error hashing media files - download path not set."
        )

    # Beware - thread pools may silently swallow exceptions!
    # https://docs.python.org/3/library/concurrent.futures.html
    with concurrent.futures.ThreadPoolExecutor() as executor:

        for root, _, files in os.walk(state.download_path):

            if config.debug:
                print_debug(f"OS walk: '{root}', {files}")
                print()

            if len(files) > 0:
                futures: list[concurrent.futures.Future] = []

                for file in files:
                    # map() doesn't cut it, or at least I couldn't get it to
                    # work with functions requiring multiple arguments.
                    future = executor.submit(
                        add_hash_to_file, config, state, Path(root) / file
                    )
                    futures.append(future)

                # Iterate over the future results so exceptions will be thrown
                for future in futures:
                    future.result()

                if config.debug:
                    print()
