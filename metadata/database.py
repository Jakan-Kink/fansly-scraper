"""Database management module.

This module provides database configuration, connection management, and migration
handling for SQLite databases. It supports both synchronous and asynchronous
operations, with proper connection pooling and event handling.

The module includes:
- Database configuration and initialization
- Migration management through Alembic
- Session management for database operations
- Logging configuration for SQLAlchemy
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from textio import SizeAndTimeRotatingFileHandler, print_error, print_info

if TYPE_CHECKING:
    from config import FanslyConfig


sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
sqlalchemy_logger.setLevel(logging.INFO)
time_handler = SizeAndTimeRotatingFileHandler(
    "sqlalchemy.log",
    maxBytes=150 * 1024 * 1024,  # 100MB
    when="h",  # Hourly rotation
    interval=2,  # Rotate every 2 hours
    backupCount=20,  # Keep 5 backups
    utc=True,  # Use UTC time
    compression="gz",  # Compress logs using gzip
    keep_uncompressed=3,  # Keep 3 uncompressed logs
)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
time_handler.setFormatter(formatter)
sqlalchemy_logger.addHandler(time_handler)
sqlalchemy_logger.propagate = False


def run_migrations_if_needed(database: Database, alembic_cfg: AlembicConfig) -> None:
    """Ensure the database is migrated to the latest schema using Alembic.

    This function checks if migrations are needed and applies them if necessary.
    It handles both initial migration setup and updates to the latest version.

    Args:
        database: Database instance to migrate
        alembic_cfg: Alembic configuration for migrations

    Note:
        - Creates alembic_version table if it doesn't exist
        - Runs all migrations if database is not initialized
        - Updates to latest version if database already has migrations
    """
    if not database.db_file.exists():
        print_info(
            f"Database file {database.db_file} does not exist. Running migrations."
        )

    print_info(f"Running migrations for database: {database.db_file}")
    with database.sync_engine.connect() as connection:
        # Check if the alembic_version table exists
        result = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
        )
        alembic_version_exists = result.fetchone() is not None

        if not alembic_version_exists:
            print_error("No alembic_version table found. Initializing migrations...")
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")  # Run all migrations
            print_info("Migrations applied successfully.")
        else:
            print_info(
                "Database is already initialized. Running migrations to the latest version..."
            )
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")
            print_info("Migrations applied successfully.")
        connection.close()


class Database:
    """Database management class.

    This class handles database configuration, connection management, and session
    creation. It provides both synchronous and asynchronous access to the database,
    with proper connection pooling and event handling.

    Attributes:
        sync_engine: SQLAlchemy engine for synchronous operations
        sync_session: Session factory for synchronous operations
        db_file: Path to the SQLite database file
        config: FanslyConfig instance containing database configuration

    Note:
        - Uses SQLite with proper type detection and thread safety
        - Configures connection pooling and event listeners
        - Provides context managers for session management
    """

    sync_engine: Engine
    sync_session: sessionmaker[Session]
    db_file: Path
    config: FanslyConfig

    def __init__(
        self,
        config: FanslyConfig,
    ) -> None:
        self.config = config
        self.db_file = Path(config.metadata_db_file)
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

    def _setup_engines_and_sessions(self) -> None:
        # Synchronous engine and session
        self.sync_engine = create_engine(
            f"sqlite:///{self.db_file}",
            connect_args={
                "check_same_thread": False,
                "detect_types": sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            },
            echo=False,  # Enable SQL logging
            echo_pool=True,  # Log connection pool activity
        )
        self.sync_session = sessionmaker(bind=self.sync_engine, expire_on_commit=False)

    def _setup_event_listeners(self) -> None:
        # Add event listeners for both sync and async engines
        for engine in [self.sync_engine]:

            @event.listens_for(engine, "connect")
            def do_connect(dbapi_connection, connection_record):
                # Set isolation level to None for explicit transaction control
                dbapi_connection.isolation_level = None

                # Temporarily disable foreign key support while fixing relationship handling
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=OFF")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

                # TODO: Re-enable foreign keys after fixing:
                # 1. Scraping user's account creation
                # 2. Order of relationship creation (ensure referenced entities exist)
                # 3. Proper error handling for missing relationships
                # cursor.execute("PRAGMA foreign_keys=ON")

            @event.listens_for(engine, "begin")
            def do_begin(conn):
                conn.exec_driver_sql("BEGIN")

    @contextmanager
    def get_sync_session(self) -> Generator[Session]:
        """
        Provide a sync session for database interaction.
        """
        with self.sync_session() as session:
            yield session
