"""Unit tests for helpers/common.py"""

import webbrowser
from unittest.mock import patch

import pytest

from helpers.common import (
    batch_list,
    get_post_id_from_request,
    is_valid_post_id,
    open_location,
)


class TestBatchList:
    """Tests for the batch_list function."""

    def test_batch_list_basic(self):
        """Test batch_list with a simple list and batch size."""
        input_list = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        batch_size = 3
        result = list(batch_list(input_list, batch_size))
        expected = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]
        assert result == expected

    def test_batch_list_exact_division(self):
        """Test batch_list when list size is exactly divisible by batch size."""
        input_list = [1, 2, 3, 4, 5, 6]
        batch_size = 2
        result = list(batch_list(input_list, batch_size))
        expected = [[1, 2], [3, 4], [5, 6]]
        assert result == expected

    def test_batch_list_single_batch(self):
        """Test batch_list when batch size is larger than list length."""
        input_list = [1, 2, 3]
        batch_size = 10
        result = list(batch_list(input_list, batch_size))
        expected = [[1, 2, 3]]
        assert result == expected

    def test_batch_list_batch_size_one(self):
        """Test batch_list with batch size of 1."""
        input_list = [1, 2, 3]
        batch_size = 1
        result = list(batch_list(input_list, batch_size))
        expected = [[1], [2], [3]]
        assert result == expected

    def test_batch_list_empty_list(self):
        """Test batch_list with an empty list."""
        input_list = []
        batch_size = 3
        result = list(batch_list(input_list, batch_size))
        expected = []
        assert result == expected

    def test_batch_list_invalid_batch_size_zero(self):
        """Test batch_list with batch size of 0 raises ValueError."""
        input_list = [1, 2, 3]
        batch_size = 0
        with pytest.raises(ValueError, match="Invalid batch size of 0 is less than 1"):
            list(batch_list(input_list, batch_size))

    def test_batch_list_invalid_batch_size_negative(self):
        """Test batch_list with negative batch size raises ValueError."""
        input_list = [1, 2, 3]
        batch_size = -5
        with pytest.raises(ValueError, match="Invalid batch size of -5 is less than 1"):
            list(batch_list(input_list, batch_size))

    def test_batch_list_mixed_types(self):
        """Test batch_list with mixed types in list."""
        input_list = [1, "two", 3.0, None, {"key": "value"}]
        batch_size = 2
        result = list(batch_list(input_list, batch_size))
        expected = [[1, "two"], [3.0, None], [{"key": "value"}]]
        assert result == expected


class TestIsValidPostId:
    """Tests for the is_valid_post_id function."""

    def test_is_valid_post_id_valid(self):
        """Test is_valid_post_id with a valid post ID."""
        assert is_valid_post_id("1234567890") is True
        assert is_valid_post_id("12345678901234567890") is True

    def test_is_valid_post_id_exactly_10_chars(self):
        """Test is_valid_post_id with exactly 10 digits."""
        assert is_valid_post_id("1234567890") is True

    def test_is_valid_post_id_too_short(self):
        """Test is_valid_post_id with less than 10 characters."""
        assert is_valid_post_id("123456789") is False
        assert is_valid_post_id("12345") is False
        assert is_valid_post_id("1") is False

    def test_is_valid_post_id_non_digit(self):
        """Test is_valid_post_id with non-digit characters."""
        assert is_valid_post_id("123456789a") is False
        assert is_valid_post_id("12345-67890") is False
        assert is_valid_post_id("abcdefghij") is False

    def test_is_valid_post_id_with_spaces(self):
        """Test is_valid_post_id with spaces."""
        assert is_valid_post_id("1234 567890") is False
        assert is_valid_post_id(" 1234567890") is False
        assert is_valid_post_id("1234567890 ") is False

    def test_is_valid_post_id_empty_string(self):
        """Test is_valid_post_id with empty string."""
        assert is_valid_post_id("") is False

    def test_is_valid_post_id_mixed_alphanumeric(self):
        """Test is_valid_post_id with mixed alphanumeric."""
        assert is_valid_post_id("1234567abc") is False


class TestGetPostIdFromRequest:
    """Tests for the get_post_id_from_request function."""

    def test_get_post_id_from_request_url(self):
        """Test get_post_id_from_request with a Fansly URL."""
        url = "https://fansly.com/post/1234567890"
        result = get_post_id_from_request(url)
        assert result == "1234567890"

    def test_get_post_id_from_request_url_with_trailing_slash(self):
        """Test get_post_id_from_request with trailing slash."""
        url = "https://fansly.com/post/1234567890/"
        result = get_post_id_from_request(url)
        # Split on "/" will give empty string as last element
        assert result == ""

    def test_get_post_id_from_request_direct_id(self):
        """Test get_post_id_from_request with direct post ID."""
        post_id = "1234567890"
        result = get_post_id_from_request(post_id)
        assert result == post_id

    def test_get_post_id_from_request_different_url(self):
        """Test get_post_id_from_request with non-Fansly URL."""
        url = "https://example.com/post/1234567890"
        result = get_post_id_from_request(url)
        # Since it doesn't start with "https://fansly.com/", return as-is
        assert result == url

    def test_get_post_id_from_request_empty_string(self):
        """Test get_post_id_from_request with empty string."""
        result = get_post_id_from_request("")
        assert result == ""


class TestOpenLocation:
    """Tests for the open_location function."""

    def test_open_location_disabled_by_flag(self, tmp_path):
        """Test open_location when open_folder_when_finished is False."""
        filepath = tmp_path / "test_file.txt"
        filepath.touch()

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                filepath, open_folder_when_finished=False, interactive=True
            )
            assert result is False
            mock_open.assert_not_called()

    def test_open_location_disabled_by_interactive(self, tmp_path):
        """Test open_location when interactive is False."""
        filepath = tmp_path / "test_file.txt"
        filepath.touch()

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                filepath, open_folder_when_finished=True, interactive=False
            )
            assert result is False
            mock_open.assert_not_called()

    def test_open_location_file_does_not_exist(self, tmp_path):
        """Test open_location with non-existent file."""
        filepath = tmp_path / "nonexistent.txt"

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                filepath, open_folder_when_finished=True, interactive=True
            )
            assert result is False
            mock_open.assert_not_called()

    def test_open_location_success_with_file(self, tmp_path):
        """Test open_location successfully opens a file."""
        filepath = tmp_path / "test_file.txt"
        filepath.touch()

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                filepath, open_folder_when_finished=True, interactive=True
            )
            assert result is True
            mock_open.assert_called_once_with(filepath.as_uri())

    def test_open_location_success_with_directory(self, tmp_path):
        """Test open_location successfully opens a directory."""
        dirpath = tmp_path / "test_dir"
        dirpath.mkdir()

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                dirpath, open_folder_when_finished=True, interactive=True
            )
            assert result is True
            mock_open.assert_called_once_with(dirpath.as_uri())

    def test_open_location_both_flags_false(self, tmp_path):
        """Test open_location when both flags are False."""
        filepath = tmp_path / "test_file.txt"
        filepath.touch()

        with patch.object(webbrowser, "open") as mock_open:
            result = open_location(
                filepath, open_folder_when_finished=False, interactive=False
            )
            assert result is False
            mock_open.assert_not_called()
