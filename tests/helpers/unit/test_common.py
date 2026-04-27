"""Unit tests for helpers/common.py — consolidated via parametrize.

Each public helper has one parametrized test that pytest expands per case
(failure granularity preserved). The original layout was 27 individual
test methods across 4 classes, almost all of `(input → expected)` shape;
parametrization collapses the per-test scaffolding while keeping the
case ids descriptive in pytest output.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.common import (
    batch_list,
    get_post_id_from_request,
    is_valid_post_id,
    open_location,
)


# ---------------------------------------------------------------------------
# batch_list — chunking semantics + error guards
# ---------------------------------------------------------------------------


_BATCH_LIST_CASES = [
    pytest.param(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        3,
        [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]],
        id="basic_with_remainder",
    ),
    pytest.param(
        [1, 2, 3, 4, 5, 6],
        2,
        [[1, 2], [3, 4], [5, 6]],
        id="exact_division",
    ),
    pytest.param([1, 2, 3], 10, [[1, 2, 3]], id="batch_larger_than_list"),
    pytest.param([1, 2, 3], 1, [[1], [2], [3]], id="batch_size_one"),
    pytest.param([], 3, [], id="empty_list"),
    pytest.param(
        [1, "two", 3.0, None, {"key": "value"}],
        2,
        [[1, "two"], [3.0, None], [{"key": "value"}]],
        id="mixed_types",
    ),
]


@pytest.mark.parametrize(("input_list", "batch_size", "expected"), _BATCH_LIST_CASES)
def test_batch_list(input_list, batch_size, expected):
    """batch_list yields fixed-size chunks with a smaller final batch on remainder."""
    assert list(batch_list(input_list, batch_size)) == expected


@pytest.mark.parametrize(
    "batch_size",
    [
        pytest.param(0, id="zero"),
        pytest.param(-5, id="negative"),
    ],
)
def test_batch_list_invalid_batch_size_raises(batch_size):
    """Batch sizes < 1 raise ValueError with the offending value embedded."""
    with pytest.raises(
        ValueError, match=f"Invalid batch size of {batch_size} is less than 1"
    ):
        list(batch_list([1, 2, 3], batch_size))


# ---------------------------------------------------------------------------
# is_valid_post_id — minimum-length all-digit check
# ---------------------------------------------------------------------------


_VALID_POST_ID_CASES = [
    # Valid: 10+ digits, all numeric
    pytest.param("1234567890", True, id="exactly_10_digits"),
    pytest.param("12345678901234567890", True, id="20_digits"),
    # Too short
    pytest.param("123456789", False, id="9_digits_too_short"),
    pytest.param("12345", False, id="5_digits_too_short"),
    pytest.param("1", False, id="1_digit_too_short"),
    pytest.param("", False, id="empty_string"),
    # Non-digit characters anywhere
    pytest.param("123456789a", False, id="trailing_letter"),
    pytest.param("12345-67890", False, id="embedded_dash"),
    pytest.param("abcdefghij", False, id="all_letters"),
    pytest.param("1234567abc", False, id="mixed_alphanumeric"),
    # Whitespace anywhere is invalid
    pytest.param("1234 567890", False, id="embedded_space"),
    pytest.param(" 1234567890", False, id="leading_space"),
    pytest.param("1234567890 ", False, id="trailing_space"),
]


@pytest.mark.parametrize(("post_id", "expected"), _VALID_POST_ID_CASES)
def test_is_valid_post_id(post_id, expected):
    """is_valid_post_id requires 10+ all-digit characters; whitespace excluded."""
    assert is_valid_post_id(post_id) is expected


# ---------------------------------------------------------------------------
# get_post_id_from_request — Fansly URL parser, otherwise passthrough
# ---------------------------------------------------------------------------


_GET_POST_ID_CASES = [
    pytest.param(
        "https://fansly.com/post/1234567890",
        "1234567890",
        id="fansly_post_url",
    ),
    pytest.param(
        # Trailing slash → split on "/" yields empty string as last element
        "https://fansly.com/post/1234567890/",
        "",
        id="fansly_post_url_trailing_slash",
    ),
    pytest.param("1234567890", "1234567890", id="direct_post_id"),
    pytest.param(
        # Non-Fansly URL → returned as-is (not parsed)
        "https://example.com/post/1234567890",
        "https://example.com/post/1234567890",
        id="non_fansly_url_passthrough",
    ),
    pytest.param("", "", id="empty_string"),
]


@pytest.mark.parametrize(("input_value", "expected"), _GET_POST_ID_CASES)
def test_get_post_id_from_request(input_value, expected):
    """Extracts post ID from Fansly URLs; non-matching input is returned verbatim."""
    assert get_post_id_from_request(input_value) == expected


# ---------------------------------------------------------------------------
# open_location — webbrowser.open dispatch with feature flag + path validation
# ---------------------------------------------------------------------------


_OpenLocationParam = tuple[
    str,  # path_kind: "file", "dir", or "missing"
    bool,  # open_folder_when_finished
    bool,  # interactive
    bool,  # expected_return
    bool,  # expected_mock_called
]


_OPEN_LOCATION_CASES: list[_OpenLocationParam] = [
    pytest.param("file", False, True, False, False, id="disabled_by_flag"),
    pytest.param("file", True, False, False, False, id="disabled_by_interactive"),
    pytest.param("missing", True, True, False, False, id="path_does_not_exist"),
    pytest.param("file", True, True, True, True, id="success_with_file"),
    pytest.param("dir", True, True, True, True, id="success_with_directory"),
    pytest.param("file", False, False, False, False, id="both_flags_false"),
]


@pytest.mark.parametrize(
    (
        "path_kind",
        "open_folder_when_finished",
        "interactive",
        "expected_return",
        "expected_mock_called",
    ),
    _OPEN_LOCATION_CASES,
)
def test_open_location(
    tmp_path,
    path_kind,
    open_folder_when_finished,
    interactive,
    expected_return,
    expected_mock_called,
):
    """open_location dispatches to webbrowser.open only when both flags + path exist."""
    if path_kind == "file":
        target: Path = tmp_path / "test_file.txt"
        target.touch()
    elif path_kind == "dir":
        target = tmp_path / "test_dir"
        target.mkdir()
    elif path_kind == "missing":
        target = tmp_path / "nonexistent.txt"
    else:  # pragma: no cover — invalid parametrize id (defensive)
        raise ValueError(f"Unknown path_kind: {path_kind!r}")

    with patch.object(webbrowser, "open") as mock_open:
        result = open_location(
            target,
            open_folder_when_finished=open_folder_when_finished,
            interactive=interactive,
        )

    assert result is expected_return
    if expected_mock_called:
        mock_open.assert_called_once_with(target.as_uri())
    else:
        mock_open.assert_not_called()
