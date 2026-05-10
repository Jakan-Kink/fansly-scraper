"""Tests for textio.prompts — async TTY-guarded interactive prompt helpers."""

from __future__ import annotations

import sys

import pytest

from tests.fixtures.textio import FakePromptSession
from textio.prompts import (
    _interpret_yn,
    _require_tty,
    _yn_suffix,
    aconfirm,
    aprompt_text,
    await_for_enter,
)


# ── _require_tty ───────────────────────────────────────────────────────────


class TestRequireTty:
    def test_passes_when_stdin_is_a_tty(self, tty):
        assert _require_tty() is None

    def test_raises_when_stdin_not_a_tty(self, monkeypatch):
        class _NonTTY:
            def isatty(self) -> bool:
                return False

        monkeypatch.setattr(sys, "stdin", _NonTTY())
        with pytest.raises(RuntimeError, match="stdin is not a TTY"):
            _require_tty()

    def test_raises_when_stdin_is_none(self, no_tty):
        with pytest.raises(RuntimeError, match="stdin is not a TTY"):
            _require_tty()


# ── _yn_suffix ─────────────────────────────────────────────────────────────


class TestYnSuffix:
    @pytest.mark.parametrize(
        ("default", "expected"),
        [(True, " [Y/n] "), (False, " [y/N] "), (None, " [y/n] ")],
    )
    def test_renders_suffix(self, default, expected):
        assert _yn_suffix(default) == expected


# ── _interpret_yn ──────────────────────────────────────────────────────────


class TestInterpretYn:
    def test_empty_with_default_true_returns_true(self):
        assert _interpret_yn("", True) is True

    def test_empty_with_default_false_returns_false(self):
        assert _interpret_yn("", False) is False

    def test_empty_no_default_returns_none(self):
        assert _interpret_yn("", None) is None

    @pytest.mark.parametrize("answer", ["y", "yes", "yup"])
    def test_y_prefix_returns_true(self, answer):
        assert _interpret_yn(answer, None) is True

    @pytest.mark.parametrize("answer", ["n", "no", "nope"])
    def test_n_prefix_returns_false(self, answer):
        assert _interpret_yn(answer, None) is False

    def test_garbage_no_default_returns_none(self):
        assert _interpret_yn("maybe", None) is None

    def test_y_prefix_overrides_default(self):
        assert _interpret_yn("yes", False) is True


# ── aconfirm ───────────────────────────────────────────────────────────────


class TestAconfirm:
    async def test_returns_true_on_yes(self, tty, fake_prompt_session):
        fake_prompt_session.append("y")
        assert await aconfirm("Continue?") is True

    async def test_returns_false_on_no(self, tty, fake_prompt_session):
        fake_prompt_session.append("n")
        assert await aconfirm("Continue?") is False

    async def test_default_used_on_empty_answer(self, tty, fake_prompt_session):
        fake_prompt_session.append("")
        assert await aconfirm("Continue?", default=True) is True

    async def test_retries_on_unparseable_then_accepts(self, tty, fake_prompt_session):
        fake_prompt_session.extend(["maybe", "y"])
        assert await aconfirm("Continue?") is True

    async def test_suffix_reflects_default(self, tty, fake_prompt_session):
        fake_prompt_session.append("y")
        await aconfirm("Continue?", default=True)
        assert FakePromptSession._captured_messages == ["Continue? [Y/n] "]


# ── aprompt_text ───────────────────────────────────────────────────────────


class TestAprompt:
    async def test_returns_stripped_answer(self, tty, fake_prompt_session):
        fake_prompt_session.append("  hello  ")
        assert await aprompt_text("Name?") == "hello"

    async def test_default_returned_when_empty(self, tty, fake_prompt_session):
        fake_prompt_session.append("")
        assert await aprompt_text("Name?", default="anon") == "anon"

    async def test_empty_no_default_returns_empty_string(
        self, tty, fake_prompt_session
    ):
        fake_prompt_session.append("")
        assert await aprompt_text("Name?") == ""


# ── await_for_enter ────────────────────────────────────────────────────────


class TestAwaitForEnter:
    async def test_completes_after_one_prompt(self, tty, fake_prompt_session):
        fake_prompt_session.append("")
        assert await await_for_enter("Press enter") is None

    async def test_discards_typed_text(self, tty, fake_prompt_session):
        fake_prompt_session.append("ignored stuff typed before enter")
        assert await await_for_enter() is None


# ── TTY guard wiring (each public helper must enforce it) ─────────────────


class TestTtyGuardEnforcedByEachHelper:
    """Each public helper must short-circuit when stdin is not a TTY."""

    async def test_aconfirm_raises_without_tty(self, no_tty):
        with pytest.raises(RuntimeError, match="stdin is not a TTY"):
            await aconfirm("?")

    async def test_aprompt_text_raises_without_tty(self, no_tty):
        with pytest.raises(RuntimeError, match="stdin is not a TTY"):
            await aprompt_text("?")

    async def test_await_for_enter_raises_without_tty(self, no_tty):
        with pytest.raises(RuntimeError, match="stdin is not a TTY"):
            await await_for_enter()
