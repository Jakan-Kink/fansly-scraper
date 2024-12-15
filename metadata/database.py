from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from config import FanslyConfig


class Database:
    engine: AsyncEngine
    session: async_sessionmaker
    db_file: Path
    config: FanslyConfig

    def __init__(self, config: FanslyConfig) -> None:

        self.config = config
        self._setup_engine_and_session()
        self._setup_event_listeners()

    async def close(self) -> None:
        await self.engine.dispose()

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _setup_engine_and_session(self) -> None:
        self.db_file = Path(self.config.get("database_file"))
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_file}",
            connect_args={
                "check_same_thread": False,
                "detect_types": sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            },
            native_datetime=True,
            echo=False,
        )

    def _setup_event_listeners(self) -> None:
        engine = self.engine

        @event.listens_for(engine.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def do_begin(conn):
            conn.exec_driver_sql("BEGIN")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession]:
        async with self.session(expire_on_commit=False) as session:
            yield session
