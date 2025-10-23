"""Unit tests for Database class initialization and configuration.

Note: Most database functionality is tested in tests/metadata/integration/test_database_integration.py
These unit tests focus on configuration validation and URL building.
"""

from unittest.mock import MagicMock, patch

import pytest

from config.fanslyconfig import FanslyConfig
from metadata.database import Database


class TestDatabaseInit:
    """Test database initialization."""

    def test_build_connection_url_basic(self, mock_config: FanslyConfig):
        """Test basic PostgreSQL URL construction."""
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
        ):
            db = Database(mock_config, skip_migrations=True)
            url = db._build_connection_url()

            assert "postgresql://" in url
            assert f"{mock_config.pg_user}:" in url
            assert f"@{mock_config.pg_host}:{mock_config.pg_port}" in url
            assert f"/{mock_config.pg_database}" in url

    def test_build_connection_url_with_password(self, mock_config: FanslyConfig):
        """Test URL construction with password encoding."""
        mock_config.pg_password = "p@ssw0rd!special"
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
        ):
            db = Database(mock_config, skip_migrations=True)
            url = db._build_connection_url()

            # Password should be URL-encoded
            assert "p%40ssw0rd%21special" in url or "p@ssw0rd!special" in url

    def test_build_connection_url_empty_password(self, mock_config: FanslyConfig):
        """Test URL construction with empty password (trust authentication)."""
        mock_config.pg_password = ""
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
        ):
            db = Database(mock_config, skip_migrations=True)
            url = db._build_connection_url()

            # Should handle empty password gracefully
            assert "postgresql://" in url
            assert f"{mock_config.pg_user}:@{mock_config.pg_host}" in url

    def test_async_url_conversion(self, mock_config: FanslyConfig):
        """Test that async URL is correctly derived from sync URL."""
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
        ):
            db = Database(mock_config, skip_migrations=True)

            # Sync URL should use psycopg2 (or no dialect specified)
            assert "postgresql://" in db.db_url

            # Async URL should use asyncpg
            assert "postgresql+asyncpg://" in db.async_db_url

    def test_init_sets_config(self, mock_config: FanslyConfig):
        """Test that init properly stores configuration."""
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
        ):
            db = Database(mock_config, skip_migrations=True)

            assert db.config == mock_config
            assert db.schema_name == "public"  # Always use public schema in PostgreSQL

    def test_skip_migrations_flag(self, mock_config: FanslyConfig):
        """Test that skip_migrations flag prevents running migrations."""
        mock_engine = MagicMock()
        mock_async_engine = MagicMock()

        with (
            patch("metadata.database.create_engine", return_value=mock_engine),
            patch(
                "metadata.database.create_async_engine", return_value=mock_async_engine
            ),
            patch("metadata.database.get_db_logger"),  # Mock logger setup
            patch.object(Database, "_setup_sql_logging"),  # Mock SQL logging
            patch("config.logging._configure_sqlalchemy_logging"),  # Mock SA logging
            patch.object(Database, "_run_migrations") as mock_migrations,
        ):
            # With skip_migrations=True, migrations should not run
            Database(mock_config, skip_migrations=True)
            mock_migrations.assert_not_called()

            # With skip_migrations=False, migrations should run
            Database(mock_config, skip_migrations=False)
            mock_migrations.assert_called_once()


# Note: Session management, transaction handling, thread safety, and other
# database operational tests are in tests/metadata/integration/test_database_integration.py
# where they can test against a real PostgreSQL database.
