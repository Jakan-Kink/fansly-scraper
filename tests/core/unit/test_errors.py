"""Unit tests for the errors module."""

import pytest

from errors import (
    API_ERROR,
    CONFIG_ERROR,
    DOWNLOAD_ERROR,
    EXIT_ABORT,
    EXIT_ERROR,
    EXIT_SUCCESS,
    SOME_USERS_FAILED,
    UNEXPECTED_ERROR,
    UPDATE_FAILED,
    UPDATE_MANUALLY,
    UPDATE_SUCCESS,
    ApiAccountInfoError,
    ApiAuthenticationError,
    ApiError,
    ConfigError,
    DownloadError,
    DuplicateCountError,
    DuplicatePageError,
    InvalidTraceLogError,
    M3U8Error,
    MediaError,
    MediaHashMismatchError,
)
from errors.mp4 import InvalidMP4Error


def test_exit_constants():
    """Test exit status constants."""
    assert EXIT_SUCCESS == 0
    assert EXIT_ERROR == -1
    assert EXIT_ABORT == -2


def test_error_type_constants():
    """Test error type constants."""
    assert UNEXPECTED_ERROR == -3
    assert API_ERROR == -4
    assert CONFIG_ERROR == -5
    assert DOWNLOAD_ERROR == -6
    assert SOME_USERS_FAILED == -7


def test_update_constants():
    """Test update status constants."""
    assert UPDATE_FAILED == -10
    assert UPDATE_MANUALLY == -11
    assert UPDATE_SUCCESS == 1


class TestDuplicateCountError:
    """Test DuplicateCountError exception."""

    def test_init(self):
        """Test initialization and message formatting."""
        count = 42
        error = DuplicateCountError(count)

        assert error.duplicate_count == count
        assert str(error) == f"Irrationally high rise in duplicates: {count}"
        assert isinstance(error, RuntimeError)


class TestConfigError:
    """Test ConfigError exception."""

    def test_init(self):
        """Test initialization with message."""
        message = "Invalid configuration"
        error = ConfigError(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)


class TestApiError:
    """Test ApiError and its subclasses."""

    def test_api_error(self):
        """Test base ApiError."""
        message = "API error occurred"
        error = ApiError(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)

    def test_api_authentication_error(self):
        """Test ApiAuthenticationError."""
        message = "Authentication failed"
        error = ApiAuthenticationError(message)

        assert str(error) == message
        assert isinstance(error, ApiError)
        assert isinstance(error, RuntimeError)

    def test_api_account_info_error(self):
        """Test ApiAccountInfoError."""
        message = "Invalid account info"
        error = ApiAccountInfoError(message)

        assert str(error) == message
        assert isinstance(error, ApiError)
        assert isinstance(error, RuntimeError)


class TestDownloadError:
    """Test DownloadError exception."""

    def test_init(self):
        """Test initialization with message."""
        message = "Download failed"
        error = DownloadError(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)


class TestMediaErrors:
    """Test MediaError and its subclasses."""

    def test_media_error(self):
        """Test base MediaError."""
        message = "Media error occurred"
        error = MediaError(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)

    def test_m3u8_error(self):
        """Test M3U8Error."""
        message = "Invalid M3U8 data"
        error = M3U8Error(message)

        assert str(error) == message
        assert isinstance(error, MediaError)
        assert isinstance(error, RuntimeError)

    def test_media_hash_mismatch_error(self):
        """Test MediaHashMismatchError."""
        message = "Hash mismatch detected"
        error = MediaHashMismatchError(message)

        assert str(error) == message
        assert isinstance(error, MediaError)
        assert isinstance(error, RuntimeError)


class TestDuplicatePageError:
    """Test DuplicatePageError exception."""

    @pytest.mark.parametrize(
        ("page_type", "page_id", "cursor", "wall_name", "expected_message"),
        [
            ("timeline", None, None, None, "All posts on timeline already in metadata"),
            ("wall", "123", None, None, "All posts on wall (123) already in metadata"),
            (
                "wall",
                None,
                "xyz",
                "username",
                "All posts on wall 'username' before xyz already in metadata",
            ),
            (
                "wall",
                "123",
                "xyz",
                None,
                "All posts on wall (123) before xyz already in metadata",
            ),
        ],
    )
    def test_init(self, page_type, page_id, cursor, wall_name, expected_message):
        """Test initialization with various parameters."""
        error = DuplicatePageError(
            page_type=page_type,
            page_id=page_id,
            cursor=cursor,
            wall_name=wall_name,
        )

        assert error.page_type == page_type
        assert error.page_id == page_id
        assert error.cursor == cursor
        assert error.wall_name == wall_name
        assert str(error) == expected_message
        assert isinstance(error, RuntimeError)


class TestInvalidTraceLogError:
    """Test InvalidTraceLogError exception."""

    def test_init(self):
        """Test initialization and message formatting."""
        level = "DEBUG"
        error = InvalidTraceLogError(level)

        assert error.level_name == level
        assert str(error) == "trace_logger only accepts TRACE level messages, got DEBUG"
        assert isinstance(error, RuntimeError)


class TestInvalidMP4Error:
    """Test InvalidMP4Error exception."""

    @pytest.mark.parametrize(
        "message",
        [
            "File is smaller than 8 bytes",
            "Missing ftyp FourCC code in header",
            "Invalid MP4 container format",
        ],
    )
    def test_init(self, message: str):
        """Test initialization with various error messages."""
        error = InvalidMP4Error(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)

    def test_without_message(self):
        """Test initialization without a message."""
        error = InvalidMP4Error()

        assert str(error) == ""
        assert isinstance(error, RuntimeError)
