"""Interactive prompts via prompt_toolkit.

Sync + async helpers for the three prompt shapes used in this codebase:
yes/no confirmation, free-text input, and press-Enter-to-continue. All
helpers drain the loguru queue (``logger.complete()``) before the prompt
appears, so log lines emitted just prior don't visually mash against
the prompt due to ``enqueue=True``'s background sink processing.

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


def confirm(question: str, *, default: bool | None = None) -> bool:
    """Synchronous yes/no prompt.

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
        answer = session.prompt(f"{question}{suffix}").strip().lower()
        result = _interpret_yn(answer, default)
        if result is not None:
            return result
        logger.error("Please enter 'y' or 'n'.")


async def aconfirm(question: str, *, default: bool | None = None) -> bool:
    """Async variant of :func:`confirm`."""
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


def prompt_text(
    question: str,
    *,
    default: str | None = None,
    completer: Completer | None = None,
) -> str:
    """Synchronous free-text prompt. Returns the user's stripped input."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession(completer=completer)
    answer = session.prompt(question).strip()
    if not answer and default is not None:
        return default
    return answer


async def aprompt_text(
    question: str,
    *,
    default: str | None = None,
    completer: Completer | None = None,
) -> str:
    """Async variant of :func:`prompt_text`."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession(completer=completer)
    answer = (await session.prompt_async(question)).strip()
    if not answer and default is not None:
        return default
    return answer


def wait_for_enter(message: str = "Press <ENTER> to continue ...") -> None:
    """Block until the user presses Enter. Discards any typed text."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession()
    session.prompt(message)


async def await_for_enter(message: str = "Press <ENTER> to continue ...") -> None:
    """Async variant of :func:`wait_for_enter`."""
    _require_tty()
    logger.complete()
    session: PromptSession[str] = PromptSession()
    await session.prompt_async(message)
