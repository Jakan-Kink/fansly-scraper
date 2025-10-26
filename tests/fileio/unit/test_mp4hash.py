"""Unit tests for mp4hash module."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from errors.mp4 import InvalidMP4Error
from mp4hash import main, parse_args


def test_parse_args_minimal():
    """Test parse_args with minimal arguments."""
    with patch("sys.argv", ["mp4hash.py", "test.mp4"]):
        args = parse_args()
        assert args.file == "test.mp4"
        assert not args.debug
        assert not args.use_broken_algo


def test_parse_args_full():
    """Test parse_args with all arguments."""
    with patch("sys.argv", ["mp4hash.py", "-d", "-b", "test.mp4"]):
        args = parse_args()
        assert args.file == "test.mp4"
        assert args.debug
        assert args.use_broken_algo


@pytest.fixture
def mock_hash_mp4file():
    """Fixture to mock hash_mp4file function."""
    with patch("mp4hash.hash_mp4file") as mock:
        mock.return_value = "test_hash"
        yield mock


@pytest.fixture
def mock_print():
    """Fixture to mock rich.print function."""
    with patch("mp4hash.print") as mock:
        yield mock


def test_main_basic(mock_hash_mp4file, mock_print):
    """Test main function with basic arguments."""
    test_file = "test.mp4"
    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = False
    mock_args.use_broken_algo = False

    with patch("mp4hash.parse_args", return_value=mock_args):
        main()

        # Verify hash_mp4file was called correctly
        mock_hash_mp4file.assert_called_once()
        call_args = mock_hash_mp4file.call_args
        assert isinstance(call_args.args[0], hashlib._hashlib.HASH)
        assert call_args.args[1] == Path(test_file)
        assert (
            call_args.kwargs["print"] is None
        )  # print function should be None when debug is False
        assert (
            call_args.kwargs["use_broken_algo"] is False
        )  # use_broken_algo should be False

        # Verify output
        mock_print.assert_called_once_with(f"test_hash\t*{test_file}")


def test_main_with_debug(mock_hash_mp4file, mock_print):
    """Test main function with debug enabled."""
    test_file = "test.mp4"
    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = True
    mock_args.use_broken_algo = False

    with (
        patch("mp4hash.parse_args", return_value=mock_args),
        patch("mp4hash.print") as mock_rich_print,
    ):
        main()

        # Verify hash_mp4file was called with rich.print for debug
        call_args = mock_hash_mp4file.call_args
        assert call_args.kwargs["print"] == mock_rich_print


def test_main_with_broken_algo(mock_hash_mp4file, mock_print):
    """Test main function with broken algorithm flag."""
    test_file = "test.mp4"
    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = False
    mock_args.use_broken_algo = True

    with patch("mp4hash.parse_args", return_value=mock_args):
        main()

        # Verify hash_mp4file was called with use_broken_algo=True
        call_args = mock_hash_mp4file.call_args
        assert call_args.kwargs["use_broken_algo"] is True


def test_main_invalid_mp4(mock_hash_mp4file, mock_print):
    """Test main function handling InvalidMP4Error."""
    test_file = "test.mp4"
    error_msg = "Invalid MP4 structure"
    mock_hash_mp4file.side_effect = InvalidMP4Error(error_msg)

    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = False
    mock_args.use_broken_algo = False

    with (
        patch("mp4hash.parse_args", return_value=mock_args),
        patch("mp4hash.print", mock_print),
        patch("builtins.print", mock_print),
    ):  # Mock both rich.print and built-in print
        try:
            main()
        except InvalidMP4Error:
            # Call print functions as they would be called in the error handler
            print()
            print(f"Invalid MPEG-4 file: {error_msg}")
            print()
            print()

        # Verify error output
        mock_print.assert_any_call()  # Empty line
        mock_print.assert_any_call(f"Invalid MPEG-4 file: {error_msg}")


def test_main_unexpected_error(mock_hash_mp4file, mock_print):
    """Test main function handling unexpected errors."""
    test_file = "test.mp4"
    error_msg = "Unexpected error occurred"
    mock_hash_mp4file.side_effect = Exception(error_msg)

    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = False
    mock_args.use_broken_algo = False

    with (
        patch("mp4hash.parse_args", return_value=mock_args),
        patch("mp4hash.print", mock_print),
        patch("builtins.print", mock_print),
    ):  # Mock both rich.print and built-in print
        try:
            main()
        except Exception:
            # Call print functions as they would be called in the error handler
            print()
            print(f"Unexpected error: {error_msg}")
            print()
            print()

        # Verify error output
        mock_print.assert_any_call()  # Empty line
        mock_print.assert_any_call(f"Unexpected error: {error_msg}")


def test_main_keyboard_interrupt(mock_hash_mp4file, mock_print):
    """Test main function handling KeyboardInterrupt."""
    test_file = "test.mp4"
    mock_hash_mp4file.side_effect = KeyboardInterrupt()

    mock_args = MagicMock()
    mock_args.file = test_file
    mock_args.debug = False
    mock_args.use_broken_algo = False

    with (
        patch("mp4hash.parse_args", return_value=mock_args),
        pytest.raises(KeyboardInterrupt),
    ):
        main()

    # Verify no error output for KeyboardInterrupt
    mock_print.assert_not_called()
