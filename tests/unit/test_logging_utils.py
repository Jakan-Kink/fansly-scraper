"""Unit tests for logging utilities."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from textio.logging import get_json_log_path, json_output


def test_get_json_log_path_default():
    """Test get_json_log_path returns default value when env var not set."""
    with patch.dict(os.environ, clear=True):
        assert get_json_log_path() == "fansly_downloader_ng_json.log"


def test_get_json_log_path_custom():
    """Test get_json_log_path returns custom value from env var."""
    custom_path = "/custom/path/log.json"
    with patch.dict(os.environ, {"LOGURU_JSON_LOG_FILE": custom_path}):
        assert get_json_log_path() == custom_path


@pytest.fixture
def mock_logger():
    """Fixture to mock logger for testing."""
    with patch("textio.logging.logger") as mock_log:
        mock_log.level = MagicMock()
        mock_log.remove = MagicMock()
        mock_log.add = MagicMock()
        mock_log.bind = MagicMock(return_value=mock_log)
        mock_log.type = MagicMock()
        yield mock_log


@pytest.fixture
def mock_handler():
    """Fixture to mock SizeTimeRotatingHandler."""
    with patch("textio.logging.SizeTimeRotatingHandler") as mock_handler_cls:
        mock_handler_instance = MagicMock()
        mock_handler_cls.return_value = mock_handler_instance
        yield mock_handler_cls


def test_json_output_basic(mock_logger, mock_handler):
    """Test basic json_output functionality."""
    level = 30
    log_type = "INFO"
    message = "Test message"

    json_output(level, log_type, message)

    # Verify logger configuration
    mock_logger.level.assert_called_once_with(log_type, no=level)
    mock_logger.remove.assert_called_once()

    # Verify handler creation
    mock_handler.assert_called_once()
    handler_kwargs = mock_handler.call_args[1]
    assert handler_kwargs["max_bytes"] == 500 * 1000 * 1000
    assert handler_kwargs["backup_count"] == 20
    assert handler_kwargs["when"] == "h"
    assert handler_kwargs["interval"] == 2
    assert handler_kwargs["compression"] == "gz"
    assert handler_kwargs["keep_uncompressed"] == 3
    assert handler_kwargs["encoding"] == "utf-8"
    assert Path(handler_kwargs["filename"]).name == "fansly_downloader_ng_json.log"

    # Verify logger.add configuration
    mock_logger.add.assert_called_once()
    add_kwargs = mock_logger.add.call_args[1]
    assert add_kwargs["level"] == log_type
    assert not add_kwargs["backtrace"]
    assert not add_kwargs["diagnose"]

    # Verify message binding and sending
    mock_logger.bind.assert_called_once_with(json=True)
    mock_logger.type.assert_called_once_with(message)


def test_json_output_invalid_level(mock_logger, mock_handler):
    """Test json_output with invalid level handles error gracefully."""
    level = "invalid"
    log_type = "INFO"
    message = "Test message"

    # Should not raise exception
    json_output(level, log_type, message)  # type: ignore

    # Rest of logger setup should still occur
    mock_logger.remove.assert_called_once()
    mock_handler.assert_called_once()
    mock_logger.add.assert_called_once()
    mock_logger.bind.assert_called_once_with(json=True)
    mock_logger.type.assert_called_once_with(message)


def test_json_output_custom_path(mock_logger, mock_handler):
    """Test json_output with custom log path from environment."""
    custom_path = "/custom/path/log.json"
    with patch.dict(os.environ, {"LOGURU_JSON_LOG_FILE": custom_path}):
        json_output(30, "INFO", "Test message")

    handler_kwargs = mock_handler.call_args[1]
    assert Path(handler_kwargs["filename"]).name == "log.json"
