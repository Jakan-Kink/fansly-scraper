"""Tests for textio/textio.py — console output, input prompts, terminal ops."""

from unittest.mock import AsyncMock, patch

import pytest

from textio.textio import (
    clear_terminal,
    input_enter_close,
    input_enter_continue,
    json_output,
    print_config,
    print_info_highlight,
    print_update,
    set_window_title,
)


class TestPrintFunctions:
    """Thin logger wrappers — verify they call without error."""

    def test_print_config(self):
        """Line 53."""
        print_config("test config message")

    def test_print_info_highlight(self):
        """Line 94."""
        print_info_highlight("test highlight message")

    def test_print_update(self):
        """Line 103."""
        print_update("test update message")

    def test_json_output(self):
        """Lines 23-44 (already partially covered, ensures full path)."""
        json_output(1, "TEST", "info level message")
        json_output(2, "TEST", "debug level message")
        json_output(99, "TEST", "unknown level defaults to INFO")
        json_output(1, "TEST", {"key": "value", "nested": True})


class TestInputFunctions:
    """Interactive input + sleep + sys.exit — patch at the edge."""

    async def test_input_enter_close_interactive(self):
        """interactive=True → await await_for_enter() then sys.exit()."""
        with (
            patch("textio.textio.await_for_enter", new_callable=AsyncMock),
            pytest.raises(SystemExit),
        ):
            await input_enter_close(interactive=True)

    async def test_input_enter_close_non_interactive(self):
        """interactive=False → await asyncio.sleep(15) then sys.exit()."""
        with (
            patch("textio.textio.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SystemExit),
        ):
            await input_enter_close(interactive=False)

    async def test_input_enter_continue_interactive(self):
        """interactive=True → await await_for_enter()."""
        with patch("textio.textio.await_for_enter", new_callable=AsyncMock):
            await input_enter_continue(interactive=True)

    async def test_input_enter_continue_non_interactive(self):
        """interactive=False → await asyncio.sleep(15)."""
        with patch("textio.textio.asyncio.sleep", new_callable=AsyncMock):
            await input_enter_continue(interactive=False)


class TestTerminalOps:
    """Terminal clearing and window title — patch subprocess.call at the edge."""

    def test_clear_terminal_macos(self):
        """Lines 142, 149-152: non-Windows (Darwin) path → calls 'clear'."""
        with (
            patch("textio.textio.platform.system", return_value="Darwin"),
            patch("textio.textio.shutil.which", return_value="/usr/bin/clear"),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            clear_terminal()
        mock_call.assert_called_once_with(["/usr/bin/clear"])

    def test_clear_terminal_windows(self):
        """Lines 144-147: Windows path → calls 'cmd /c cls'."""
        with (
            patch("textio.textio.platform.system", return_value="Windows"),
            patch("textio.textio.shutil.which", return_value="C:\\Windows\\cmd.exe"),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            clear_terminal()
        mock_call.assert_called_once_with(["C:\\Windows\\cmd.exe", "/c", "cls"])

    def test_clear_terminal_no_clear_binary(self):
        """Lines 150-151: which('clear') returns None → no subprocess call."""
        with (
            patch("textio.textio.platform.system", return_value="Linux"),
            patch("textio.textio.shutil.which", return_value=None),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            clear_terminal()
        mock_call.assert_not_called()

    def test_set_window_title_macos(self):
        """Lines 157, 164-167: Darwin path → calls printf with escape sequence."""
        with (
            patch("textio.textio.platform.system", return_value="Darwin"),
            patch("textio.textio.shutil.which", return_value="/usr/bin/printf"),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            set_window_title("Test Title")
        mock_call.assert_called_once()
        args = mock_call.call_args[0][0]
        assert args[0] == "/usr/bin/printf"
        assert "Test Title" in args[1]

    def test_set_window_title_windows(self):
        """Lines 159-162: Windows path → calls cmd /c title."""
        with (
            patch("textio.textio.platform.system", return_value="Windows"),
            patch("textio.textio.shutil.which", return_value="C:\\Windows\\cmd.exe"),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            set_window_title("Test Title")
        mock_call.assert_called_once_with(
            ["C:\\Windows\\cmd.exe", "/c", "title", "Test Title"],
        )

    def test_set_window_title_no_printf(self):
        """Lines 165-166: which('printf') returns None → no subprocess call."""
        with (
            patch("textio.textio.platform.system", return_value="Linux"),
            patch("textio.textio.shutil.which", return_value=None),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            set_window_title("Test Title")
        mock_call.assert_not_called()

    def test_set_window_title_unsupported_platform(self):
        """Line 157-167: platform not Windows/Linux/Darwin → no action."""
        with (
            patch("textio.textio.platform.system", return_value="FreeBSD"),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            set_window_title("Test Title")
        mock_call.assert_not_called()
