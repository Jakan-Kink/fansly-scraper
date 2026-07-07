"""Unit tests for Database class initialization and configuration.

Note: Most database functionality is tested in tests/metadata/integration/test_database_integration.py
These unit tests focus on configuration validation and URL building.

Uses real SQLAlchemy engines (lazy, don't connect until used) to avoid mocking
engine internals that break when Database.__init__ registers event listeners.
"""

from unittest.mock import patch

import pytest

from config.fanslyconfig import FanslyConfig
from metadata.database import Database


class TestDatabaseInit:
    """Test database initialization.

    These tests create real SQLAlchemy engines pointing at a non-existent DB name.
    Since engines are lazy (no connection until first query) and we use
    skip_migrations=True, no actual database connection is made.
    """

    @pytest.mark.parametrize(
        ("password", "expected_fragments"),
        [
            pytest.param(
                None,  # keep the fixture's default password
                [
                    ("postgresql://",),
                    ("{user}:",),
                    ("@{host}:{port}",),
                    ("/{database}",),
                ],
                id="basic_default_password",
            ),
            pytest.param(
                "p@ssw0rd!special",
                # Password should be URL-encoded (either form accepted)
                [("p%40ssw0rd%21special", "p@ssw0rd!special")],
                id="special_chars_password_encoded",
            ),
            pytest.param(
                "",
                # Should handle empty password gracefully (trust authentication)
                [
                    ("postgresql://",),
                    ("{user}:@{host}",),
                ],
                id="empty_password_trust_auth",
            ),
        ],
    )
    def test_build_connection_url(
        self,
        mock_config: FanslyConfig,
        password: str | None,
        expected_fragments: list[tuple[str, ...]],
    ) -> None:
        """PostgreSQL URL construction: basic parts, password encoding, empty password.

        Each expected fragment is a tuple of acceptable alternatives (usually one);
        placeholders are filled from the config fixture before matching.
        """
        if password is not None:
            mock_config.pg_password = password
        db = Database(mock_config, skip_migrations=True)
        url = db._build_connection_url()

        parts = {
            "user": mock_config.pg_user,
            "host": mock_config.pg_host,
            "port": mock_config.pg_port,
            "database": mock_config.pg_database,
        }
        for alternatives in expected_fragments:
            assert any(alt.format(**parts) in url for alt in alternatives), (
                f"none of {alternatives} found in {url!r}"
            )

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


# Note: Session management, transaction handling, thread safety, and other
# database operational tests are in tests/metadata/integration/test_database_integration.py
# where they can test against a real PostgreSQL database.
