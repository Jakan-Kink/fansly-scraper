from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from config import FanslyConfig


class Database:
    async_engine: AsyncEngine
    sync_engine: Engine
    async_session: async_sessionmaker[AsyncSession]
    sync_session: sessionmaker[Session]
    db_file: Path
    config: FanslyConfig

    def __init__(self, config: FanslyConfig) -> None:
        self.config = config
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

    async def close(self) -> None:
        await self.async_engine.dispose()
        self.sync_engine.dispose()

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _setup_engines_and_sessions(self) -> None:
        if self.config.metadata_db_file is None:
            self.config.metadata_db_file = "metadata_db.sqlite3"
        self.db_file = Path(self.config.metadata_db_file)

        # Synchronous engine and session
        self.sync_engine = create_engine(
            f"sqlite:///{self.db_file}",
            connect_args={
                "check_same_thread": False,
                "detect_types": sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            },
            echo=False,
        )
        self.sync_session = sessionmaker(bind=self.sync_engine, expire_on_commit=False)

        # Asynchronous engine and session
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_file}",
            connect_args={
                "check_same_thread": False,
                "detect_types": sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            },
            native_datetime=True,
            echo=False,
        )
        self.async_session = async_sessionmaker(
            bind=self.async_engine, expire_on_commit=False
        )

    def _setup_event_listeners(self) -> None:
        # Add event listeners for both sync and async engines
        for engine in [self.sync_engine, self.async_engine.sync_engine]:

            @event.listens_for(engine, "connect")
            def do_connect(dbapi_connection, connection_record):
                dbapi_connection.isolation_level = None

            @event.listens_for(engine, "begin")
            def do_begin(conn):
                conn.exec_driver_sql("BEGIN")

    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
        """
        Provide an async session for database interaction.
        """
        async with self.async_session() as session:
            yield session

    @contextmanager
    def get_sync_session(self) -> Generator[Session]:
        """
        Provide a sync session for database interaction.
        """
        with self.sync_session() as session:
            yield session
