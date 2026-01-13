"""Tests for alembic/env.py by importing and calling functions directly.

Target uncovered lines:
- Lines 50-62: run_migrations_offline() function body
- Line 76: ValueError when URL missing in offline mode
- Line 109: if connection.in_transaction() - else branch (when not in transaction)
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Load env.py as a module so we can import its functions
env_path = Path(__file__).parent.parent.parent / "alembic" / "env.py"
spec = importlib.util.spec_from_file_location("alembic_env_module", env_path)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load spec for {env_path}")
env_module = importlib.util.module_from_spec(spec)
sys.modules["alembic_env_module"] = env_module
# Don't exec yet - will do it in each test with proper mocks


class TestEnvOfflineMode:
    """Test run_migrations_offline() by calling it directly with mocks."""

    def test_offline_mode_executes_with_url(self):
        """Test offline mode function body execution - covers lines 50-62."""
        # Mock the alembic context and config
        with (
            patch("alembic.context") as mock_context,
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")
            # Setup mocks
            mock_config = MagicMock()
            mock_config.get_main_option.return_value = (
                "postgresql://user:pass@localhost/db"
            )
            mock_context.config = mock_config
            # Set offline mode so the module auto-calls run_migrations_offline()
            mock_context.is_offline_mode.return_value = True

            # Execute the module - this will automatically call run_migrations_offline()
            # at lines 108-111 since is_offline_mode() returns True
            spec.loader.exec_module(env_module)

            # Verify configure was called with offline parameters (not called_once due to auto-exec)
            # The module auto-executes at lines 108-111, so we just verify the call happened
            assert mock_context.configure.called
            call_kwargs = mock_context.configure.call_args[1]
            assert call_kwargs["url"] == "postgresql://user:pass@localhost/db"
            assert call_kwargs["literal_binds"] is True
            assert call_kwargs["dialect_opts"] == {"paramstyle": "named"}

            # Verify migrations were run
            mock_context.begin_transaction.assert_called_once()
            mock_context.run_migrations.assert_called_once()

    def test_offline_mode_raises_on_missing_url(self):
        """Test offline mode raises ValueError when URL is missing - covers line 76."""
        with (
            patch("alembic.context") as mock_context,
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")

            # Setup mocks - no URL
            mock_config = MagicMock()
            mock_config.get_main_option.return_value = None
            mock_context.config = mock_config
            # Set offline mode so the module auto-calls run_migrations_offline()
            mock_context.is_offline_mode.return_value = True

            # Execute module - this will automatically call run_migrations_offline()
            # which should raise ValueError due to missing URL
            with pytest.raises(ValueError, match="No database URL configured"):
                spec.loader.exec_module(env_module)


class TestEnvOnlineMode:
    """Test run_migrations_online() and do_run_migrations() by calling directly with mocks."""

    def test_online_mode_creates_engine_when_no_connection(self):
        """Test online mode creates engine when no connection provided - covers line 109."""
        with (
            patch("alembic.context") as mock_context,
            patch("sqlalchemy.create_engine") as mock_create_engine,
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")

            # Setup mocks
            mock_config = MagicMock()
            mock_config.get_main_option.return_value = (
                "postgresql://user:pass@localhost/db"
            )
            mock_config.attributes = {}  # No connection
            mock_context.config = mock_config

            mock_engine = MagicMock()
            mock_conn = MagicMock()
            mock_conn.in_transaction.return_value = False  # Not in transaction
            mock_engine.connect.return_value.__enter__.return_value = mock_conn
            mock_create_engine.return_value = mock_engine

            # Execute module
            spec.loader.exec_module(env_module)

            # Call function
            env_module.run_migrations_online()

            # Verify engine was created
            mock_create_engine.assert_called_once()
            # Verify commit was NOT called (line 109 else branch)
            mock_conn.commit.assert_not_called()

    def test_online_mode_uses_provided_connection(self):
        """Test online mode uses connection from attributes."""
        with (
            patch("alembic.context") as mock_context,
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")
            mock_config = MagicMock()
            mock_conn = MagicMock()
            mock_conn.in_transaction.return_value = True  # In transaction
            mock_config.attributes = {"connection": mock_conn}
            mock_context.config = mock_config

            # Execute module
            spec.loader.exec_module(env_module)

            # Call function
            env_module.run_migrations_online()

            # Verify commit was called (line 109 if branch)
            mock_conn.commit.assert_called_once()

    def test_do_run_migrations_commits_when_in_transaction(self):
        """Test do_run_migrations commits when in transaction - covers line 109."""
        with (
            patch("alembic.context"),
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")
            spec.loader.exec_module(env_module)

            # Create mock connection
            mock_conn = MagicMock()
            mock_conn.in_transaction.return_value = True

            # Call do_run_migrations directly
            env_module.do_run_migrations(mock_conn)

            # Verify commit was called
            mock_conn.commit.assert_called_once()

    def test_do_run_migrations_no_commit_when_not_in_transaction(self):
        """Test do_run_migrations doesn't commit when not in transaction - covers line 109 else."""
        with (
            patch("alembic.context"),
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")

            # Execute module
            spec.loader.exec_module(env_module)

            # Create mock connection NOT in transaction
            mock_conn = MagicMock()
            mock_conn.in_transaction.return_value = False

            # Call function
            env_module.do_run_migrations(mock_conn)

            # Verify commit was NOT called
            mock_conn.commit.assert_not_called()

    def test_online_mode_raises_on_missing_url(self):
        """Test online mode raises ValueError when URL is missing - covers line 76."""
        with (
            patch("alembic.context") as mock_context,
            patch.dict(
                sys.modules, {"metadata.base": MagicMock(), "metadata": MagicMock()}
            ),
        ):
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {env_path}")

            # Setup mocks - no URL and no connection
            mock_config = MagicMock()
            mock_config.get_main_option.return_value = None
            mock_config.attributes = {}  # No connection
            mock_context.config = mock_config
            # Set online mode so the module auto-calls run_migrations_online()
            mock_context.is_offline_mode.return_value = False

            # Execute module - this will automatically call run_migrations_online()
            # which should raise ValueError due to missing URL
            with pytest.raises(ValueError, match="No database URL configured"):
                spec.loader.exec_module(env_module)
