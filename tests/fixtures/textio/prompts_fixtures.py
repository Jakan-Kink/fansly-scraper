"""Fixtures for tests that exercise textio.prompts directly.

The ``tty`` fixture forces ``_require_tty`` to pass; ``fake_prompt_session``
replaces prompt_toolkit.PromptSession with a scripted fake whose
``prompt_async`` pops scripted answers from a shared list.
"""

from __future__ import annotations

import sys

import pytest

from textio import prompts


class FakePromptSession:
    """Stand-in for prompt_toolkit.PromptSession that returns scripted answers.

    Tests push answers into the shared ``_answers`` list (managed by the
    ``fake_prompt_session`` fixture). Each call to ``prompt_async`` pops
    one from the front and records the prompt message.
    """

    _answers: list[str] = []
    _captured_messages: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def prompt_async(self, message: str) -> str:
        FakePromptSession._captured_messages.append(message)
        return FakePromptSession._answers.pop(0)


@pytest.fixture
def fake_prompt_session(monkeypatch):
    """Replace PromptSession in textio.prompts with a scripted fake.

    Yields the answer queue. Push string answers in test order; each
    ``prompt_async`` call pops one. Tests that exercise retry loops
    queue multiple answers.

    Also exposes ``FakePromptSession._captured_messages`` for assertions
    on the rendered prompt text (e.g. the [Y/n] suffix wiring).
    """
    FakePromptSession._answers = []
    FakePromptSession._captured_messages = []
    monkeypatch.setattr(prompts, "PromptSession", FakePromptSession)
    return FakePromptSession._answers


@pytest.fixture
def tty(monkeypatch):
    """Force ``_require_tty`` to pass by faking an interactive stdin."""

    class _TTYStub:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _TTYStub())


@pytest.fixture
def no_tty(monkeypatch):
    """Force ``_require_tty`` to raise by clearing sys.stdin.

    Useful for tests that verify a caller's TTY-gating short-circuits
    correctly when run under automation / pytest (no interactive stdin).
    """
    monkeypatch.setattr(sys, "stdin", None)
