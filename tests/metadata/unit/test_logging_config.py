"""Tests for metadata/logging_config.py — asyncpg DatabaseLogger and get_caller_info."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from metadata.logging_config import (
    DatabaseLogger,
    get_caller_info,
    get_db_logger,
)
from metadata.models import Account, Media
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGetCallerInfo:
    """Tests for get_caller_info — stack inspection."""

    def test_returns_string(self):
        result = get_caller_info()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_calling_function(self):
        result = get_caller_info()
        assert "test_includes_calling_function" in result

    def test_includes_file_path(self):
        result = get_caller_info()
        assert "test_logging_config.py" in result

    def test_skips_outside_repo_vendored_and_wrapper_frames(
        self, tmp_path, monkeypatch
    ):
        """Frames outside cwd (stdlib), inside cwd/.venv (vendored), and in
        _SKIP_FUNCS must all be excluded. The first in-repo, non-vendored
        frame wins."""
        monkeypatch.chdir(tmp_path)
        real_caller = tmp_path / "daemon" / "state.py"
        fake_stack = [
            SimpleNamespace(filename="<current>", function="get_caller_info", lineno=1),
            # stdlib — outside cwd → relative_to raises ValueError → skip
            SimpleNamespace(
                filename="/usr/lib/python3.14/asyncio/events.py",
                function="_run",
                lineno=94,
            ),
            # vendored — inside cwd but path contains .venv → skip
            SimpleNamespace(
                filename=str(tmp_path / ".venv" / "lib" / "asyncpg" / "pool.py"),
                function="fetch",
                lineno=10,
            ),
            # vendored — site-packages segment → skip
            SimpleNamespace(
                filename=str(
                    tmp_path / ".venv" / "lib" / "site-packages" / "gql" / "client.py"
                ),
                function="execute",
                lineno=20,
            ),
            # wrapper func name → skip even though path is in-repo
            SimpleNamespace(
                filename=str(tmp_path / "metadata" / "entity_store.py"),
                function="async_wrapper",
                lineno=30,
            ),
            # real caller — in-repo, not vendored, not a skip func → win
            SimpleNamespace(
                filename=str(real_caller),
                function="mark_creator_processed",
                lineno=42,
            ),
        ]
        with patch("metadata.logging_config.inspect.stack", return_value=fake_stack):
            result = get_caller_info()
        assert "mark_creator_processed" in result
        assert "daemon/state.py" in result
        assert ":42" in result

    def test_unknown_caller_when_all_frames_excluded(self, tmp_path, monkeypatch):
        """When every frame is stdlib/vendored/wrapper, return the sentinel
        instead of falling back to a stdlib frame (which was the original
        bug — asyncio.events._run getting attributed as app code)."""
        monkeypatch.chdir(tmp_path)
        fake_stack = [
            SimpleNamespace(filename="<current>", function="get_caller_info", lineno=1),
            SimpleNamespace(
                filename="/usr/lib/python3.14/asyncio/events.py",
                function="_run",
                lineno=94,
            ),
            SimpleNamespace(
                filename=str(tmp_path / ".venv" / "lib" / "asyncpg" / "pool.py"),
                function="_execute",
                lineno=20,
            ),
            SimpleNamespace(
                filename=str(tmp_path / ".venv" / "lib" / "site-packages" / "gql.py"),
                function="execute",
                lineno=30,
            ),
        ]
        with patch("metadata.logging_config.inspect.stack", return_value=fake_stack):
            result = get_caller_info()
        assert result == "<unknown caller>"


class TestDatabaseLoggerUnit:
    """Unit tests for DatabaseLogger — no DB needed."""

    def test_initial_stats(self):
        logger = DatabaseLogger()
        stats = logger.get_stats()
        assert stats == {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def test_get_stats_returns_copy(self):
        logger = DatabaseLogger()
        stats = logger.get_stats()
        stats["queries"] = 999
        assert logger.get_stats()["queries"] == 0

    def test_reset_stats(self):
        logger = DatabaseLogger()
        logger._stats["queries"] = 42
        logger._stats["total_time"] = 1.5
        logger.reset_stats()
        assert logger.get_stats()["queries"] == 0
        assert logger.get_stats()["total_time"] == 0.0

    def test_cleanup_resets_stats(self):
        logger = DatabaseLogger()
        logger._stats["errors"] = 10
        logger.cleanup()
        assert logger.get_stats()["errors"] == 0

    def test_query_logger_callback_increments_queries(self):
        logger = DatabaseLogger()
        record = SimpleNamespace(
            query="SELECT 1",
            args=(),
            timeout=None,
            elapsed=0.005,
            exception=None,
            conn_addr=("localhost", 5432),
            conn_params=None,
        )
        logger.query_logger_callback(record)
        assert logger.get_stats()["queries"] == 1
        assert logger.get_stats()["total_time"] == pytest.approx(0.005)

    def test_query_logger_callback_tracks_errors(self):
        logger = DatabaseLogger()
        record = SimpleNamespace(
            query="SELECT bad_column FROM nonexistent",
            args=(),
            timeout=None,
            elapsed=0.001,
            exception=RuntimeError("relation does not exist"),
            conn_addr=("localhost", 5432),
            conn_params=None,
        )
        logger.query_logger_callback(record)
        assert logger.get_stats()["errors"] == 1
        assert logger.get_stats()["queries"] == 1

    def test_query_logger_callback_detects_slow_query(self):
        logger = DatabaseLogger()
        record = SimpleNamespace(
            query="SELECT pg_sleep(1)",
            args=(),
            timeout=None,
            elapsed=0.15,  # >100ms threshold
            exception=None,
            conn_addr=("localhost", 5432),
            conn_params=None,
        )
        logger.query_logger_callback(record)
        assert logger.get_stats()["slow_queries"] == 1

    def test_query_logger_callback_fast_query_not_slow(self):
        logger = DatabaseLogger()
        record = SimpleNamespace(
            query="SELECT 1",
            args=(),
            timeout=None,
            elapsed=0.001,  # <100ms
            exception=None,
            conn_addr=("localhost", 5432),
            conn_params=None,
        )
        logger.query_logger_callback(record)
        assert logger.get_stats()["slow_queries"] == 0


class TestGetDbLogger:
    """Tests for the singleton factory."""

    def test_returns_database_logger(self):
        logger = get_db_logger()
        assert isinstance(logger, DatabaseLogger)

    def test_returns_same_instance(self):
        a = get_db_logger()
        b = get_db_logger()
        assert a is b


class TestDatabaseLoggerIntegration:
    """Integration tests — verify logging is wired through entity_store."""

    @pytest.mark.asyncio
    async def test_queries_tracked_via_entity_store(self, entity_store):
        """Queries through entity_store should be tracked by the logger.

        The entity_store fixture calls db.create_entity_store() which creates
        the pool with init=_init_pg_connection, which wires up the logger.
        """
        logger = get_db_logger()
        before = logger.get_stats()["queries"]

        # Execute a real query through the entity store
        account = Account(id=snowflake_id(), username="logger_test_user")
        await entity_store.save(account)
        found = await entity_store.get(Account, account.id)

        after = logger.get_stats()["queries"]
        assert after > before, (
            f"Expected query count to increase from {before}, got {after}"
        )
        assert found is not None

    @pytest.mark.asyncio
    async def test_total_time_accumulates(self, entity_store, test_account):
        """total_time should increase as queries execute."""
        logger = get_db_logger()
        before = logger.get_stats()["total_time"]

        media = Media(id=snowflake_id(), accountId=test_account.id)
        await entity_store.save(media)

        after = logger.get_stats()["total_time"]
        assert after >= before
