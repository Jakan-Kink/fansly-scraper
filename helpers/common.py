"""Common Utility Functions"""

import webbrowser
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_timestamp(v: Any) -> Any:
    """Coerce a Fansly timestamp to a UTC datetime.

    Accepts epoch milliseconds or seconds (int/float, disambiguated by the
    ``> 1e10`` boundary), an ISO 8601 string, or an existing datetime.
    Returns the value unchanged when it is None or an unrecognised type.

    Shared by metadata model validators, daemon polling filters, and the
    startup banner -- the single source for Fansly's epoch-ms convention.
    """
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        if v > 1e10:
            v = v / 1000
        return datetime.fromtimestamp(v, UTC)
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def batch_list(input_list: Sequence[Any], batch_size: int) -> Iterable[list[Any]]:
    """Yield successive n-sized batches from input_list.

    :param input_list: An arbitrary list to split into equal-sized chunks.
    :type input_list: list[Any]

    :param batch_size: The number of elements in a chunk to
        split the list into. Batch size must be >= 1.
    :type batch_size: int

    :return: An iterable of sub-lists of size `batch_size`.
    :rtype: Iterable[list[Any]]
    """
    if batch_size < 1:
        raise ValueError(
            f"batch_list(): Invalid batch size of {batch_size} is less than 1."
        )

    for i in range(0, len(input_list), batch_size):
        yield list(input_list[i : i + batch_size])


def is_valid_post_id(post_id: str) -> bool:
    """Validates a Fansly post ID.

    Valid post IDs must:

    - only contain digits
    - be longer or equal to 10 characters
    - not contain spaces

    :param post_id: The post ID string to validate.
    :type post_id: str

    :return: True or False.
    :rtype: bool
    """
    return all(
        [
            post_id.isdigit(),
            len(post_id) >= 10,
            not any(char.isspace() for char in post_id),
        ]
    )


def get_post_id_from_request(requested_post: str) -> str:
    """Strips post_id from a post link if necessary.
    Otherwise, the post_id is returned directly

    :param requested_post: The request made by the user.
    :type requested_post: str

    :return: The extracted post_id.
    :rtype: str
    """
    post_id = requested_post
    if requested_post.startswith("https://fansly.com/"):
        post_id = requested_post.rsplit("/", maxsplit=1)[-1]
    return post_id


def open_location(
    filepath: Path, open_folder_when_finished: bool, interactive: bool
) -> bool:
    """Opens the download directory in the platform's respective
    file manager application once the download process has finished.

    Uses webbrowser.open() for cross-platform compatibility.

    :param filepath: The base path of all downloads.
    :type filepath: Path
    :param open_folder_when_finished: Open the folder or do nothing.
    :type open_folder_when_finished: bool
    :param interactive: Running interactively or not.
        Folder will not be opened when set to False.
    :type interactive: bool

    :return: True when the folder was opened or False otherwise.
    :rtype: bool
    """
    if not open_folder_when_finished or not interactive:
        return False

    if not filepath.is_file() and not filepath.is_dir():
        return False

    # Use webbrowser.open() for cross-platform file manager opening
    # Convert Path to file:// URL for proper handling
    file_url = filepath.as_uri()
    webbrowser.open(file_url)

    return True
