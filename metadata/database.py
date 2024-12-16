from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from textio import print_error, print_info

if TYPE_CHECKING:
    from config import FanslyConfig


def run_migrations_if_needed(database: Database, alembic_cfg: AlembicConfig) -> None:
    """
    Ensures the database is migrated to the latest schema using Alembic.
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
                dbapi_connection.isolation_level = None

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
