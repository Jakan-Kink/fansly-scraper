"""Fixtures for integration-testing ``fansly_downloader_ng.main``.

Provides the scaffolding that main() needs to run end-to-end without touching
the real filesystem (for config loading), the real command line (for arg
parsing), or production-pace anti-detection delays (for timing jitter).

Naming convention: fixtures are PUBLIC (no leading underscore) so they can be
requested by any test file. Each has a focused responsibility; compose them
together in an integration test signature:

    async def test_main_xxx(
        config_with_database,
        bypass_load_config,
        minimal_argv,
        fast_timing,
        ...,
    ):
        # config is set up, sys.argv is test-safe, timing_jitter returns 0
        result = await main(config)
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def bypass_load_config(monkeypatch):
    """Make ``fansly_downloader_ng.load_config`` a no-op.

    main() calls ``load_config(config)`` which reads config.yaml. Integration
    tests typically use the in-memory ``config_with_database`` fixture;
    real YAML loading is orthogonal and covered by tests/config/unit/test_loader.py.

    Each test must also:
    - Set ``config.config_path`` to a real (writable) path before calling main() —
      ``map_args_to_config`` requires it (config/args.py:804).
    - Call ``init_logging_config(config)`` — normally load_config does this,
      and downstream ``set_debug_enabled`` (inside ``map_args_to_config``)
      uses the logging module's global ``_config`` for its isinstance check
      (config/logging.py:660).
    """
    monkeypatch.setattr("fansly_downloader_ng.load_config", lambda _cfg: None)


@pytest.fixture
def minimal_argv(monkeypatch):
    """Control sys.argv so ``parse_args`` doesn't consume pytest's arguments.

    Uses the non-interactive / no-prompt flags to avoid any input() prompts.
    Individual tests can override by calling ``monkeypatch.setattr`` again
    before invoking main().
    """
    monkeypatch.setattr(sys, "argv", ["fansly_downloader_ng.py", "-ni", "-npox"])


@pytest.fixture
def fast_timing(monkeypatch):
    """Zero out anti-detection timing jitters so integration tests run fast.

    Production code scatters ``asyncio.sleep(timing_jitter(0.4, 0.75))`` or
    ``asyncio.sleep(timing_jitter(2, 4))`` calls between API hits for
    anti-detection pacing; across a full creator flow those add up to ~30
    seconds. Integration tests don't exercise timing behavior (that's a
    deliberate production concern with its own unit tests in
    ``tests/helpers/unit/test_timer.py``), so patching the leaf
    ``timing_jitter`` helper to return 0 at every import site makes
    integration tests runnable in seconds.

    ``timing_jitter`` is a 1-liner wrapper around ``random.uniform``; patching
    it does not affect assertions about orchestration, HTTP call sequences,
    or DB state — those are what integration tests verify.

    ``from helpers.timer import timing_jitter`` gives each importing module
    its own bound name, so patching only the source doesn't cover call
    sites. The list below matches every current ``from helpers.timer import
    timing_jitter`` in production code; add new sites as they appear.
    """
    targets = (
        "helpers.timer.timing_jitter",  # source
        "fansly_downloader_ng.timing_jitter",
        "api.fansly.timing_jitter",
        "download.account.timing_jitter",
        "download.media.timing_jitter",
        "download.messages.timing_jitter",
        "download.timeline.timing_jitter",
        "download.wall.timing_jitter",
    )
    for target in targets:
        monkeypatch.setattr(target, lambda _min, _max: 0.0, raising=False)


__all__ = ["bypass_load_config", "fast_timing", "minimal_argv"]
