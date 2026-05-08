"""Pytest fixtures for FanslyConfig-based unit tests.

These fixtures live here (not inlined in test files) so multiple test
modules can reuse them and an audit can spot drift between callers.

Names are deliberately distinct from the global ``config`` fixture in
``tests/fixtures/database/`` so opt-in is explicit and there is no
silent shadowing.

Fixtures:
- ``unit_config_path`` — temp YAML path for save_config_or_raise round-trips
- ``unit_config`` — minimal real ``FanslyConfig`` with a long-enough token+UA
- ``no_display`` — monkeypatches RateLimiterDisplay.start to a no-op
- ``validation_config`` — real FanslyConfig wired for ``config/validation.py`` tests
"""

from pathlib import Path

import pytest

from config.fanslyconfig import FanslyConfig
from config.modes import DownloadMode


@pytest.fixture
def unit_config_path(tmp_path):
    """Create a temporary config file path (yaml format)."""
    return tmp_path / "config.yaml"


@pytest.fixture
def unit_config(unit_config_path):
    """Create a FanslyConfig instance for unit testing (no database)."""
    cfg = FanslyConfig(program_version="1.0.0")
    cfg.config_path = unit_config_path
    # Token must be >= 50 chars to pass token_is_valid() check
    cfg.token = "test_token_long_enough_to_pass_validation_checks_here"
    cfg.user_agent = "test_user_agent_long_enough_for_validation"
    cfg.check_key = "test_check_key"
    cfg.user_names = {"user1", "user2"}
    return cfg


@pytest.fixture
def no_display(monkeypatch):
    """Suppress the RateLimiterDisplay background thread.

    ``FanslyConfig.get_api`` constructs a ``RateLimiterDisplay`` and
    ``setup_api`` calls its ``.start()`` which spawns a daemon thread
    running a Rich live progress display. In tests this would (a) hold
    a thread open that pytest can't reliably join, (b) write to
    stderr/stdout in ways that break pytest capture, (c) potentially
    leak between tests. Patching ``.start()`` to a no-op disables the
    thread spawn while leaving the rest of the real wiring intact.
    """
    monkeypatch.setattr(
        "api.rate_limiter_display.RateLimiterDisplay.start",
        lambda _self: None,
    )


@pytest.fixture
def validation_config(tmp_path):
    """Real ``FanslyConfig`` configured for ``config/validation.py`` tests.

    Every field starts at a known real value so the production
    ``token_is_valid()`` / ``useragent_is_valid()`` methods return True
    by default — tests that want the "invalid" branch of those checks
    override the field explicitly (e.g., ``config.token = "short"``).

    The ``config_path`` points at ``tmp_path / "config.yaml"`` so any
    ``save_config_or_raise`` path runs real YAML I/O into a throwaway
    directory — no mocks. Because asserted-on values like ``token`` and
    ``user_agent`` round-trip through YAML and get re-loaded in some
    validators, the size of the strings matches Fansly's real shape
    (60-char token, Mozilla/5.0 UA).
    """
    config = FanslyConfig(program_version="0.13.0-test")
    config.config_path = tmp_path / "config.yaml"
    config.interactive = False
    config.user_names = {"validuser1", "validuser2"}
    # token_is_valid() requires len >= 50 and no "ReplaceMe".
    config.token = "a" * 60
    # useragent_is_valid() requires len >= 40 and no "ReplaceMe".
    config.user_agent = "Mozilla/5.0 " + "A" * 60
    config.check_key = "check-key-placeholder-123"
    config.download_directory = Path.cwd()
    config.download_mode = DownloadMode.TIMELINE
    config.username = None
    config.password = None
    return config
