"""Errors/Exceptions"""

from typing import Any


# region Constants

EXIT_SUCCESS: int = 0
EXIT_ERROR: int = -1
EXIT_ABORT: int = -2
UNEXPECTED_ERROR: int = -3
API_ERROR: int = -4
CONFIG_ERROR: int = -5
DOWNLOAD_ERROR: int = -6
SOME_USERS_FAILED: int = -7
UPDATE_FAILED: int = -10
UPDATE_MANUALLY: int = -11
UPDATE_SUCCESS: int = 1

# endregion

# region Exceptions


class DuplicateCountError(RuntimeError):
    """The purpose of this error is to prevent unnecessary computation or requests to fansly.
    Will stop downloading, after reaching either the base DUPLICATE_THRESHOLD or 20% of total content.

    To maintain logical consistency, users have the option to disable this feature;
    e.g. a user downloads only 20% of a creator's media and then cancels the download, afterwards tries
    to update that folder -> the first 20% will report completed -> cancels the download -> other 80% missing
    """

    def __init__(self, duplicate_count: int) -> None:
        self.duplicate_count = duplicate_count
        self.message = f"Irrationally high rise in duplicates: {duplicate_count}"
        super().__init__(self.message)


class ConfigError(RuntimeError):
    """This error is raised when configuration data is invalid.

    Invalid data may have been provided by config.ini or command-line.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class ApiError(RuntimeError):
    """This error is raised when the Fansly API yields no or invalid results.

    This may be caused by authentication issues (invalid token),
    invalid user names or - in rare cases - changes to the Fansly API itself.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class ApiAuthenticationError(ApiError):
    """This specific error is raised when the Fansly API
    yields an authentication error.

    This may primarily be caused by an invalid token.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class ApiAccountInfoError(ApiError):
    """This specific error is raised when the Fansly API
    for account information yields invalid results.

    This may primarily be caused by an invalid user name.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class DownloadError(RuntimeError):
    """This error is raised when a media item could not be downloaded.

    This may be caused by network errors, proxy errors, server outages
    and so on.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class MediaError(RuntimeError):
    """This error is raised when data of a media item is invalid.

    This may be by programming errors or trace back to problems in
    Fansly API calls.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class M3U8Error(MediaError):
    """This error is raised when M3U8 data is invalid eg.
    both no audio and no video.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class MediaHashMismatchError(MediaError):
    """Raised when a media file's hash doesn't match the database record."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)


class DuplicatePageError(RuntimeError):
    """Raised when all posts on a page are already in metadata."""

    def __init__(
        self,
        page_type: str,
        page_id: str | None = None,
        cursor: str | None = None,
        wall_name: str | None = None,
    ):
        self.page_type = page_type
        self.page_id = page_id
        self.cursor = cursor
        self.wall_name = wall_name
        self.message = (
            f"All posts on {page_type}"
            + (f" '{wall_name}'" if wall_name else "")
            + (f" ({page_id})" if page_id and not wall_name else "")
            + (f" before {cursor}" if cursor else "")
            + " already in metadata"
        )
        super().__init__(self.message)


class InvalidTraceLogError(RuntimeError):
    """Raised when trace_logger is used with a level other than TRACE.

    The trace_logger is specifically for TRACE level messages only.
    Using any other level (DEBUG, INFO, etc.) is a programming error.
    """

    def __init__(self, level_name: str):
        self.level_name = level_name
        self.message = (
            f"trace_logger only accepts TRACE level messages, got {level_name}"
        )
        super().__init__(self.message)


# endregion


__all__ = [
    "API_ERROR",
    "CONFIG_ERROR",
    "DOWNLOAD_ERROR",
    "EXIT_ABORT",
    "EXIT_ERROR",
    "EXIT_SUCCESS",
    "SOME_USERS_FAILED",
    "UNEXPECTED_ERROR",
    "UPDATE_FAILED",
    "UPDATE_MANUALLY",
    "UPDATE_SUCCESS",
    "ApiAccountInfoError",
    "ApiAuthenticationError",
    "ApiError",
    "ConfigError",
    "DownloadError",
    "DuplicateCountError",
    "DuplicatePageError",
    "InvalidTraceLogError",
    "M3U8Error",
    "MediaError",
    "MediaHashMismatchError",
]
