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
    StashCleanupWarning,
    StashConnectionError,
    StashError,
    StashGraphQLError,
    StashServerError,
    StubNotImplementedError,
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


@pytest.mark.parametrize(
    ("exc_class", "args", "expected_str", "expected_bases", "expected_attrs"),
    [
        pytest.param(
            DuplicateCountError,
            (42,),
            "Irrationally high rise in duplicates: 42",
            (RuntimeError,),
            {"duplicate_count": 42},
            id="duplicate-count-error",
        ),
        pytest.param(
            ConfigError,
            ("Invalid configuration",),
            "Invalid configuration",
            (RuntimeError,),
            {},
            id="config-error",
        ),
        pytest.param(
            ApiError,
            ("API error occurred",),
            "API error occurred",
            (RuntimeError,),
            {},
            id="api-error",
        ),
        pytest.param(
            ApiAuthenticationError,
            ("Authentication failed",),
            "Authentication failed",
            (ApiError, RuntimeError),
            {},
            id="api-authentication-error",
        ),
        pytest.param(
            ApiAccountInfoError,
            ("Invalid account info",),
            "Invalid account info",
            (ApiError, RuntimeError),
            {},
            id="api-account-info-error",
        ),
        pytest.param(
            DownloadError,
            ("Download failed",),
            "Download failed",
            (RuntimeError,),
            {},
            id="download-error",
        ),
        pytest.param(
            MediaError,
            ("Media error occurred",),
            "Media error occurred",
            (RuntimeError,),
            {},
            id="media-error",
        ),
        pytest.param(
            M3U8Error,
            ("Invalid M3U8 data",),
            "Invalid M3U8 data",
            (MediaError, RuntimeError),
            {},
            id="m3u8-error",
        ),
        pytest.param(
            MediaHashMismatchError,
            ("Hash mismatch detected",),
            "Hash mismatch detected",
            (MediaError, RuntimeError),
            {},
            id="media-hash-mismatch-error",
        ),
        pytest.param(
            InvalidTraceLogError,
            ("DEBUG",),
            "trace_logger only accepts TRACE level messages, got DEBUG",
            (RuntimeError,),
            {"level_name": "DEBUG"},
            id="invalid-trace-log-error",
        ),
        pytest.param(
            StashError,
            ("Stash error occurred",),
            "Stash error occurred",
            (RuntimeError,),
            {},
            id="stash-error",
        ),
        pytest.param(
            StashGraphQLError,
            ("GraphQL query failed",),
            "GraphQL query failed",
            (StashError, RuntimeError),
            {},
            id="stash-graphql-error",
        ),
        pytest.param(
            StashConnectionError,
            ("Cannot connect to Stash",),
            "Cannot connect to Stash",
            (StashError, RuntimeError),
            {},
            id="stash-connection-error",
        ),
        pytest.param(
            StashServerError,
            ("Stash server error 500",),
            "Stash server error 500",
            (StashError, RuntimeError),
            {},
            id="stash-server-error",
        ),
        pytest.param(
            StashCleanupWarning,
            ("Failed to cleanup test data",),
            "Failed to cleanup test data",
            (UserWarning,),
            {},
            id="stash-cleanup-warning",
        ),
    ],
)
def test_exception_construction(
    exc_class: type[BaseException],
    args: tuple[object, ...],
    expected_str: str,
    expected_bases: tuple[type, ...],
    expected_attrs: dict[str, object],
) -> None:
    """Each exception formats its message and preserves its inheritance chain."""
    error = exc_class(*args)

    assert str(error) == expected_str
    for base in expected_bases:
        assert isinstance(error, base)
    for attr, value in expected_attrs.items():
        assert getattr(error, attr) == value


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
    def test_init(self, message: str) -> None:
        """Test initialization with various error messages."""
        error = InvalidMP4Error(message)

        assert str(error) == message
        assert isinstance(error, RuntimeError)

    def test_without_message(self):
        """Test initialization without a message."""
        error = InvalidMP4Error()

        assert str(error) == ""
        assert isinstance(error, RuntimeError)


class TestStubNotImplementedError:
    """Test StubNotImplementedError message construction branches."""

    def test_message_without_junction_table(self):
        """No junction_table → branch at __init__ line 195 takes False."""

        class FakeModel:
            pass

        err = StubNotImplementedError(FakeModel, 12345)
        msg = str(err)
        assert "No stub creator for FakeModel (id=12345)" in msg
        assert "junction" not in msg
        assert "Implement FakeModel.create_stub" in msg

    def test_message_with_junction_table(self):
        """junction_table set → branch True; covers errors/__init__.py:196."""

        class FakeModel:
            pass

        err = StubNotImplementedError(FakeModel, 67890, junction_table="post_hashtags")
        msg = str(err)
        assert "No stub creator for FakeModel (id=67890)" in msg
        assert "referenced by post_hashtags junction" in msg
        assert "Implement FakeModel.create_stub" in msg
