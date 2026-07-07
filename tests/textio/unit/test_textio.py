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

    async def test_input_enter_close_non_interactive(
        self, monkeypatch, scaled_async_sleep_recording
    ):
        """interactive=False → await asyncio.sleep(15) then sys.exit()."""
        monkeypatch.setattr("textio.textio.asyncio.sleep", scaled_async_sleep_recording)
        with pytest.raises(SystemExit):
            await input_enter_close(interactive=False)
        assert 15 in scaled_async_sleep_recording.calls

    async def test_input_enter_continue_interactive(self):
        """interactive=True → await await_for_enter()."""
        with patch("textio.textio.await_for_enter", new_callable=AsyncMock):
            await input_enter_continue(interactive=True)

    async def test_input_enter_continue_non_interactive(
        self, monkeypatch, scaled_async_sleep_recording
    ):
        """interactive=False → await asyncio.sleep(15)."""
        monkeypatch.setattr("textio.textio.asyncio.sleep", scaled_async_sleep_recording)
        await input_enter_continue(interactive=False)
        assert 15 in scaled_async_sleep_recording.calls


class TestTerminalOps:
    """Terminal clearing and window title — patch subprocess.call at the edge."""

    @pytest.mark.parametrize(
        ("platform_name", "which_result", "expected_call"),
        [
            pytest.param(
                "Darwin",
                "/usr/bin/clear",
                ["/usr/bin/clear"],
                id="macos_calls_clear",
            ),
            pytest.param(
                "Windows",
                "C:\\Windows\\cmd.exe",
                ["C:\\Windows\\cmd.exe", "/c", "cls"],
                id="windows_calls_cmd_cls",
            ),
            pytest.param("Linux", None, None, id="no_clear_binary"),
            pytest.param("Windows", None, None, id="windows_no_cmd_binary"),
        ],
    )
    def test_clear_terminal(
        self,
        platform_name: str,
        which_result: str | None,
        expected_call: list[str] | None,
    ) -> None:
        """Lines 142-152: Windows → 'cmd /c cls', Darwin/Linux → 'clear';
        which() returning None on either branch means no subprocess call."""
        with (
            patch("textio.textio.platform.system", return_value=platform_name),
            patch("textio.textio.shutil.which", return_value=which_result),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            clear_terminal()
        if expected_call is None:
            mock_call.assert_not_called()
        else:
            mock_call.assert_called_once_with(expected_call)

    @pytest.mark.parametrize(
        ("platform_name", "which_result", "expected_call"),
        [
            pytest.param(
                "Darwin",
                "/usr/bin/printf",
                ["/usr/bin/printf", r"\33]0;Test Title\a"],
                id="macos_calls_printf",
            ),
            pytest.param(
                "Windows",
                "C:\\Windows\\cmd.exe",
                ["C:\\Windows\\cmd.exe", "/c", "title", "Test Title"],
                id="windows_calls_cmd_title",
            ),
            pytest.param("Windows", None, None, id="windows_no_cmd_binary"),
            pytest.param("Linux", None, None, id="no_printf_binary"),
            pytest.param("FreeBSD", None, None, id="unsupported_platform"),
        ],
    )
    def test_set_window_title(
        self,
        platform_name: str,
        which_result: str | None,
        expected_call: list[str] | None,
    ) -> None:
        """Lines 157-167: Windows → 'cmd /c title', Darwin/Linux → printf
        escape sequence containing the title; which() returning None or an
        unsupported platform (FreeBSD never reaches which()) means no
        subprocess call."""
        with (
            patch("textio.textio.platform.system", return_value=platform_name),
            patch("textio.textio.shutil.which", return_value=which_result),
            patch("textio.textio.subprocess.call") as mock_call,
        ):
            set_window_title("Test Title")
        if expected_call is None:
            mock_call.assert_not_called()
        else:
            mock_call.assert_called_once_with(expected_call)
            assert "Test Title" in expected_call[-1]
