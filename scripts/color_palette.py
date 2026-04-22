#!/usr/bin/env python3
"""Display the log-level color palette used by fansly_downloader_ng.

Shows three sections:

  1. **Currently used levels** — what ``_CUSTOM_LEVELS`` in
     ``config/logging.py`` maps to today, including the icon, the
     loguru markup string (hyphen-form), the Rich style string
     (underscore-form), and a live sample of both renderings so you
     can compare.
  2. **ANSI 16 palette** — every color that works in both loguru and
     Rich, rendered with its name.
  3. **Style modifiers** — bold/italic/etc. rendered in combination
     with a color, so you can see the compound effect before wiring
     it into ``_CUSTOM_LEVELS``.

Usage:
    poetry run python scripts/color_palette.py
    # or, if you prefer an ipython session:
    ipython -i scripts/color_palette.py

Run this after any edit to ``_CUSTOM_LEVELS`` to verify Rich will not
reject the new style strings at Theme construction time (the failure
mode that previously fell back silently to plain stdout and made
progress bars stripe with log lines).
"""

from __future__ import annotations

import sys
from pathlib import Path


# Allow running the script directly without needing PYTHONPATH set.
# Project root is one level up from scripts/.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from config.logging import _CUSTOM_LEVELS  # noqa: E402


console = Console()


def _render_sample(style: str, label: str) -> Text:
    """Build a sample Text rendered with the given Rich style string.

    Falls back to a visible error marker when Rich refuses to parse
    the style — that's the exact failure mode Theme construction hits
    when ``_CUSTOM_LEVELS`` carries a hyphen-form name.
    """
    try:
        return Text(label, style=style)
    except Exception as exc:  # noqa: BLE001 — showing any error to the user
        return Text(f"[INVALID: {exc}]", style="bold red on yellow")


def _show_used_levels() -> None:
    """Print the current ``_CUSTOM_LEVELS`` with both markup variants."""
    console.rule("[bold]Currently-used levels (config/logging.py : _CUSTOM_LEVELS)")

    table = Table(
        show_header=True,
        header_style="bold",
        caption="Each level needs BOTH variants — loguru uses hyphens, "
        "Rich uses underscores.",
    )
    table.add_column("key")
    table.add_column("name")
    table.add_column("icon")
    table.add_column("loguru color")
    table.add_column("rich_style")
    table.add_column("rich sample")

    for key, data in _CUSTOM_LEVELS.items():
        name = str(data.get("name", key))
        icon = str(data.get("icon", "●"))
        loguru_color = str(data.get("color", "—"))
        rich_style = str(data.get("rich_style", "—"))
        sample = _render_sample(rich_style, f"{icon}  {name}  example log message")
        table.add_row(key, name, icon, loguru_color, rich_style, sample)

    console.print(table)

    # Also render each level via loguru so you can eyeball the ANSI path
    # independently — useful when a level renders fine under Rich but
    # looks wrong in log files that went through loguru's colorize path.
    # ``<level>`` picks up the level's registered color (loguru's config
    # side); don't put the color tag in the message itself — loguru won't
    # parse tags inside user messages by default.
    console.rule("[bold]Same levels rendered through loguru's ANSI pipeline")
    tmp_sink_id = logger.add(
        sys.stdout,
        colorize=True,
        format="<level>{message}</level>",
    )
    try:
        for key, data in _CUSTOM_LEVELS.items():
            level_name = str(data.get("name", key))
            icon = str(data.get("icon", "●"))
            logger.log(level_name, f"{icon}  {level_name}  example log message")
    finally:
        logger.remove(tmp_sink_id)


def _show_ansi16_palette() -> None:
    """Show the 16 ANSI-standard colors supported by both libraries."""
    console.rule("[bold]ANSI 16 palette (works in both loguru and Rich)")

    ansi_base = [
        "black",
        "red",
        "green",
        "yellow",
        "blue",
        "magenta",
        "cyan",
        "white",
    ]
    ansi_bright = [f"bright_{c}" for c in ansi_base]

    table = Table(show_header=True, header_style="bold")
    table.add_column("Rich name")
    table.add_column("loguru markup")
    table.add_column("sample")

    for rich_name in ansi_base + ansi_bright:
        # Rich "bright_magenta" → loguru "<light-magenta>"; Rich "red" → "<red>".
        loguru_form = "<" + rich_name.replace("bright_", "light-") + ">"
        table.add_row(
            rich_name,
            loguru_form,
            _render_sample(rich_name, "sample text"),
        )
    console.print(table)


def _show_modifiers() -> None:
    """Show common style modifiers compounded with a base color."""
    console.rule("[bold]Style modifiers (combinable with any color)")

    modifiers = ["bold", "dim", "italic", "underline", "blink", "reverse", "strike"]
    table = Table(show_header=True, header_style="bold")
    table.add_column("modifier")
    table.add_column("Rich form")
    table.add_column("sample (paired with red)")
    for m in modifiers:
        style = f"{m} red"
        table.add_row(m, style, _render_sample(style, "sample text"))
    console.print(table)

    console.rule("[bold]Combining multiple modifiers")
    for combo in (
        "bold bright_magenta",
        "italic underline cyan",
        "bold reverse red",
        "dim italic bright_green",
    ):
        console.print(_render_sample(combo, f"  {combo}  "))


def main() -> None:
    _show_used_levels()
    console.print()
    _show_ansi16_palette()
    console.print()
    _show_modifiers()
    console.print()
    console.rule("[bold]Notes")
    console.print(
        "- Edit `config/logging.py:_CUSTOM_LEVELS` to change a level's colors.\n"
        "- Keep `color` (loguru) and `rich_style` (Rich) semantically aligned.\n"
        "- Run this script again to confirm the new pair renders the way you want.\n"
        "- If Rich rejects your `rich_style`, the 'rich sample' column will say "
        "[bold red]INVALID[/bold red] — fix before running the main app or you'll "
        "hit the silent-fallback path again."
    )


if __name__ == "__main__":
    main()
