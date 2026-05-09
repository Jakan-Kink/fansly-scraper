"""Async interactive prompts via prompt_toolkit.

Three prompt shapes — yes/no confirmation, free-text input, and
press-Enter-to-continue — exposed as async helpers (``aconfirm``,
``aprompt_text``, ``await_for_enter``). All helpers drain the loguru
queue (``logger.complete()``) before the prompt appears, so log lines
emitted just prior don't visually mash against the prompt due to
``enqueue=True``'s background sink processing.

Async-only by design: this codebase runs entirely under ``asyncio.run()``
at the top, so a sync helper would invariably nest event loops
(prompt_toolkit's ``PromptSession.prompt()`` calls ``asyncio.run()``
internally and raises in nested contexts). All callers should be
async; convert sync callers via the upward-propagation pattern rather
than reaching for sync prompt helpers.

Non-TTY callers raise RuntimeError — interactive prompts shouldn't run
under automation, and silent fallback would mask bugs in the
config.interactive gating elsewhere.
"""

import sys

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer


def _require_tty() -> None:
    """Refuse to prompt when stdin isn't an interactive terminal."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise RuntimeError(
            "Interactive prompt requested but stdin is not a TTY. "
            "Check the calling code's config.interactive gating."
        )


def _yn_suffix(default: bool | None) -> str:
    """Render ' [Y/n] ' / ' [y/N] ' / ' [y/n] ' depending on default."""
    if default is True:
        return " [Y/n] "
    if default is False:
        return " [y/N] "
    return " [y/n] "


def _interpret_yn(answer: str, default: bool | None) -> bool | None:
    """Map a stripped answer to True/False, or None to retry."""
    if not answer and default is not None:
        return default
    if answer.startswith("y"):
        return True
    if answer.startswith("n"):
        return False
    return None


async def aconfirm(question: str, *, default: bool | None = None) -> bool:
    """Async yes/no prompt.

    Args:
        question: The question text (no trailing space — the helper adds
            the [y/n] hint).
        default: If set, an empty answer (just Enter) returns this value.
            Capitalises the matching letter in the hint.

    Returns:
        True for yes, False for no.
    """
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession()
    suffix = _yn_suffix(default)
    while True:
        answer = (await session.prompt_async(f"{question}{suffix}")).strip().lower()
        result = _interpret_yn(answer, default)
        if result is not None:
            return result
        logger.error("Please enter 'y' or 'n'.")


async def aprompt_text(
    question: str,
    *,
    default: str | None = None,
    completer: Completer | None = None,
) -> str:
    """Async free-text prompt. Returns the user's stripped input."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession(completer=completer)
    answer = (await session.prompt_async(question)).strip()
    if not answer and default is not None:
        return default
    return answer


async def await_for_enter(message: str = "Press <ENTER> to continue ...") -> None:
    """Block until the user presses Enter. Discards any typed text."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession()
    await session.prompt_async(message)
