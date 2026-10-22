"""Unit tests for Database class initialization and configuration.

Note: Most database functionality is tested in tests/metadata/integration/test_database_integration.py
These unit tests focus on configuration validation and URL building.

Uses real SQLAlchemy engines (lazy, don't connect until used) to avoid mocking
engine internals that break when Database.__init__ registers event listeners.
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from config.fanslyconfig import FanslyConfig
from metadata.database import Database
from metadata.models import FanslyObject
from metadata.tables import metadata as table_metadata


class TestDatabaseInit:
    """Test database initialization.

    These tests create real SQLAlchemy engines pointing at a non-existent DB name.
    Since engines are lazy (no connection until first query) and we use
    skip_migrations=True, no actual database connection is made.
    """

    @pytest.mark.parametrize(
        ("user", "password"),
        [
            pytest.param("fansly_user", "", id="empty_password_trust_auth"),
            pytest.param("fansly_user", "p@ss w0rd!+/:%", id="special_chars_and_space"),
            pytest.param(None, "secret", id="no_user_uses_driver_default"),
        ],
    )
    def test_build_connection_url(
        self,
        mock_config: FanslyConfig,
        monkeypatch: pytest.MonkeyPatch,
        user: str | None,
        password: str,
    ) -> None:
        """The URL round-trips every credential character and carries the SSL settings.

        Parsed with SQLAlchemy's ``make_url`` -- the same parser ``create_engine``
        uses -- so a space in the password must not come back as ``+``.
        """
        monkeypatch.delenv("FANSLY_PG_PASSWORD", raising=False)
        mock_config.pg_user = user
        mock_config.pg_password = password
        mock_config.pg_sslmode = "verify-full"
        mock_config.pg_sslrootcert = Path("/certs/my ca.pem")

        url = make_url(Database(mock_config, skip_migrations=True).db_url)

        assert url.drivername == "postgresql"
        assert url.username == (user or "")
        assert url.password == password
        assert (url.host, url.port, url.database) == (
            mock_config.pg_host,
            mock_config.pg_port,
            mock_config.pg_database,
        )
        assert dict(url.query) == {
            "sslmode": "verify-full",
            "sslrootcert": "/certs/my ca.pem",
        }

    def test_init_sets_config(self, mock_config: FanslyConfig) -> None:
        """Test that init properly stores configuration."""
        db = Database(mock_config, skip_migrations=True)

        assert db.config == mock_config
        assert "postgresql://" in db.db_url

    def test_skip_migrations_flag(self, mock_config: FanslyConfig) -> None:
        """Test that skip_migrations flag prevents running migrations."""
        with patch.object(Database, "_run_migrations") as mock_migrations:
            # With skip_migrations=True, migrations should not run
            Database(mock_config, skip_migrations=True)
            mock_migrations.assert_not_called()

            # With skip_migrations=False, migrations should run
            Database(mock_config, skip_migrations=False)
            mock_migrations.assert_called_once()


class TestDatabaseSslWiring:
    """``pg_ssl*`` settings reach the Alembic engine and every asyncpg pool."""

    @pytest.mark.asyncio
    async def test_unsatisfiable_ssl_blocks_engine_and_pool(
        self, config: FanslyConfig, tmp_path: Path
    ) -> None:
        """``verify-full`` with a missing root cert must fail both connection paths.

        Holds whether or not the server offers SSL: without SSL both drivers
        refuse ``verify-full``; with SSL the root cert cannot be loaded.
        """
        config.pg_sslmode = "verify-full"
        config.pg_sslrootcert = tmp_path / "missing-root.crt"
        db = Database(config, skip_migrations=True)
        try:
            with pytest.raises(OperationalError), db._sync_engine.connect():
                pass
            with pytest.raises(FileNotFoundError):
                await db.create_entity_store()
        finally:
            FanslyObject._store = None
            db.close_sync()

    @pytest.mark.asyncio
    async def test_ssl_settings_reach_main_and_thread_local_pools(
        self,
        config: FanslyConfig,
        tmp_path: Path,
        asyncpg_create_pool_spy: list[dict[str, Any]],
    ) -> None:
        """``sslmode=disable`` connects; both pools receive the libpq SSL params.

        The thread-local pool is built from ``DbConfig`` in a worker thread with
        its own event loop, the same path cross-loop callers take.
        """
        root_cert = tmp_path / "unused-root.crt"
        config.pg_sslmode = "disable"
        config.pg_sslrootcert = root_cert
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()

            def worker() -> int:
                async def probe() -> int:
                    try:
                        pool = await store._get_pool()
                        return await pool.fetchval("SELECT 1")
                    finally:
                        await store.close_thread_resources()

                return asyncio.run(probe())

            assert await asyncio.to_thread(worker) == 1

            assert len(asyncpg_create_pool_spy) == 2
            for call in asyncpg_create_pool_spy:
                assert parse_qs(urlparse(call["dsn"]).query) == {
                    "sslmode": ["disable"],
                    "sslrootcert": [str(root_cert)],
                }
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


# Note: Session management, transaction handling, thread safety, and other
# database operational tests are in tests/metadata/integration/test_database_integration.py
# where they can test against a real PostgreSQL database.
