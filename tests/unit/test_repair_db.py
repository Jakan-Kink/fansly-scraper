"""Unit tests for repair_db module."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repair_db import main, parse_args


def test_parse_args():
    """Test parse_args with database path."""
    test_path = "/path/to/db.sqlite"
    with patch("sys.argv", ["repair_db.py", test_path]):
        args = parse_args()
        assert args.db_path == test_path


@pytest.fixture
def mock_sqlite():
    """Fixture to mock sqlite3 connection and cursor."""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("repair_db.sqlite3") as mock_sqlite3:
        mock_sqlite3.connect.return_value = mock_conn
        mock_sqlite3.PARSE_DECLTYPES = sqlite3.PARSE_DECLTYPES
        mock_sqlite3.PARSE_COLNAMES = sqlite3.PARSE_COLNAMES
        mock_sqlite3.Row = sqlite3.Row
        yield mock_sqlite3, mock_conn, mock_cursor


@pytest.fixture
def mock_print():
    """Fixture to mock print function."""
    with patch("repair_db.print") as mock:
        yield mock


def test_main_file_not_found(mock_print):
    """Test main when database file doesn't exist."""
    test_path = "/nonexistent/db.sqlite"
    with patch("sys.argv", ["repair_db.py", test_path]):
        with patch("repair_db.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance

            result = main()

            assert result == 1
            mock_print.assert_called_once_with(
                f"Error: Database file not found: {test_path}"
            )


def test_main_success(mock_sqlite, mock_print):
    """Test main with successful database repair."""
    mock_sqlite3, mock_conn, mock_cursor = mock_sqlite
    test_path = "/path/to/db.sqlite"

    # Mock data for mismatched rows
    mock_rows = [
        {
            "incorrect_id": "123",
            "correct_id": "456",
            "local_filename": "file_456.mp4",
            "content_hash": "hash123",
            "is_downloaded": 1,
        },
        {
            "incorrect_id": "789",
            "correct_id": "012",
            "local_filename": "file_012.mp4",
            "content_hash": "hash789",
            "is_downloaded": 0,
        },
    ]
    mock_cursor.fetchall.return_value = [MagicMock(**row) for row in mock_rows]

    with patch("sys.argv", ["repair_db.py", test_path]):
        with patch("repair_db.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            result = main()

            # Verify success
            assert result == 0

            # Verify database operations
            mock_cursor.execute.assert_any_call(
                "SELECT incorrect.id AS incorrect_id, correct.id AS correct_id, "
                "incorrect.local_filename, incorrect.content_hash, incorrect.is_downloaded "
                "FROM media AS incorrect "
                "JOIN media AS correct "
                "ON incorrect.local_filename LIKE '%' || correct.id || '%' "
                "WHERE incorrect.local_filename NOT LIKE '%' || incorrect.id || '%'"
            )

            # Verify updates for each row
            for row in mock_rows:
                mock_cursor.execute.assert_any_call(
                    "UPDATE media SET local_filename = ?, content_hash = ?, "
                    "is_downloaded = ? WHERE id = ?",
                    (
                        row["local_filename"],
                        row["content_hash"],
                        row["is_downloaded"],
                        row["correct_id"],
                    ),
                )
                mock_cursor.execute.assert_any_call(
                    "DELETE FROM media WHERE id = ?",
                    (row["incorrect_id"],),
                )

            # Verify transaction management
            mock_conn.commit.assert_called_once()
            mock_conn.close.assert_called_once()


def test_main_database_error(mock_sqlite, mock_print):
    """Test main handling database error."""
    mock_sqlite3, mock_conn, mock_cursor = mock_sqlite
    test_path = "/path/to/db.sqlite"

    # Simulate database error
    error_msg = "Database error"
    mock_cursor.execute.side_effect = sqlite3.Error(error_msg)

    with patch("sys.argv", ["repair_db.py", test_path]):
        with patch("repair_db.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            result = main()

            # Verify error handling
            assert result == 1
            mock_conn.rollback.assert_called_once()
            mock_conn.close.assert_called_once()
            mock_print.assert_any_call(f"An error occurred: {error_msg}")


def test_main_general_exception(mock_sqlite, mock_print):
    """Test main handling general exception."""
    mock_sqlite3, mock_conn, mock_cursor = mock_sqlite
    test_path = "/path/to/db.sqlite"

    # Simulate general error
    error_msg = "Unexpected error"
    mock_cursor.execute.side_effect = Exception(error_msg)

    with patch("sys.argv", ["repair_db.py", test_path]):
        with patch("repair_db.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            result = main()

            # Verify error handling
            assert result == 1
            mock_conn.rollback.assert_called_once()
            mock_conn.close.assert_called_once()
            mock_print.assert_any_call(f"An error occurred: {error_msg}")
