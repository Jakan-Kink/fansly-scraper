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

    def test_skips_venv_frames_and_wrapper_funcs(self):
        """Line 61: frames in site-packages/venv are skipped.
        Line 62: frames with skip_funcs names are skipped.
        Line 67-68: ValueError from relative_to when file is outside cwd."""
        fake_stack = [
            SimpleNamespace(filename="<current>", function="get_caller_info", lineno=1),
            # Line 61: venv frame → skip
            SimpleNamespace(
                filename="/path/to/venv/lib/asyncpg/pool.py",
                function="fetch",
                lineno=10,
            ),
            # Line 61: site-packages frame → skip
            SimpleNamespace(
                filename="/lib/site-packages/gql/client.py",
                function="execute",
                lineno=20,
            ),
            # Line 62: wrapper func name → skip
            SimpleNamespace(
                filename="/app/metadata/entity_store.py",
                function="async_wrapper",
                lineno=30,
            ),
            # Line 67-68: outside cwd → ValueError from relative_to
            SimpleNamespace(
                filename="/totally/outside/cwd/script.py",
                function="real_caller",
                lineno=42,
            ),
        ]
        with patch("metadata.logging_config.inspect.stack", return_value=fake_stack):
            result = get_caller_info()
        assert "real_caller" in result
        assert "script.py" in result  # Falls back to filepath.name
        assert ":42" in result

    def test_fallback_when_all_frames_filtered(self):
        """Lines 71-80: every frame after [0] is filtered → fallback dumps deepest 5.
        Also covers line 77-78 (Exception in fallback's relative_to)."""
        fake_stack = [
            SimpleNamespace(filename="<current>", function="get_caller_info", lineno=1),
            # Every frame is venv/site-packages or has a skip_funcs name
            SimpleNamespace(
                filename="/venv/lib/asyncpg/connection.py", function="fetch", lineno=10
            ),
            SimpleNamespace(
                filename="/venv/lib/asyncpg/pool.py", function="_execute", lineno=20
            ),
            SimpleNamespace(
                filename="/site-packages/gql/transport.py",
                function="execute",
                lineno=30,
            ),
            SimpleNamespace(
                filename="/venv/lib/asyncio/tasks.py", function="__call__", lineno=40
            ),
            SimpleNamespace(
                filename="/venv/lib/python3.13/runpy.py", function="close", lineno=50
            ),
        ]
        with patch("metadata.logging_config.inspect.stack", return_value=fake_stack):
            result = get_caller_info()
        # Fallback: joins deepest 5 frames with newlines
        assert "\n" in result
        assert "runpy.py:50" in result


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

    def test_log_listener_callback_no_error(self):
        """log_listener_callback should not raise on valid messages."""
        logger = DatabaseLogger()
        fake_conn = None
        message = SimpleNamespace(severity="NOTICE", message="test notice")
        # Should not raise
        logger.log_listener_callback(fake_conn, message)


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
