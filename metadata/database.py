"""Database management for PostgreSQL.

This module provides:
- asyncpg connection pool for EntityStore (all runtime data access)
- Sync SA engine for Alembic migrations only
- EntityStore initialization and lifecycle management
"""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import asyncpg
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event

from config import db_logger

from .entity_store import PostgresEntityStore


if TYPE_CHECKING:
    from config import FanslyConfig

# Set up database logging
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)


class Database:
    """PostgreSQL database management.

    Architecture:
    - asyncpg pool → PostgresEntityStore → Pydantic models (all runtime access)
    - SA sync engine → Alembic migrations only (no ORM, no sessions)
    """

    def __init__(
        self,
        config: FanslyConfig,
        *,
        creator_name: str | None = None,
        skip_migrations: bool = False,
    ) -> None:
        self.config = config
        self.creator_name = creator_name

        # EntityStore (created via create_entity_store())
        self._entity_store: PostgresEntityStore | None = None
        self._asyncpg_pool: asyncpg.Pool | None = None

        # Cleanup tracking
        self._cleanup_done = threading.Event()
        self._cleanup_lock = threading.Lock()

        # Build connection URL
        self.db_url = self._build_connection_url()

        db_logger.info("Initializing PostgreSQL database")

        # Sync SA engine — used ONLY for Alembic migrations
        self._sync_engine = create_engine(
            self.db_url,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )

        # Set timezone to UTC on sync engine connections
        @event.listens_for(self._sync_engine, "connect")
        def set_sync_timezone(dbapi_conn: Any, _connection_record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("SET timezone='UTC'")
            cursor.close()

        # Run Alembic migrations
        if not skip_migrations:
            self._run_migrations()

        # Register cleanup
        atexit.register(self.close_sync)

        db_logger.info("PostgreSQL database initialized")

    def _build_connection_url(self) -> str:
        """Build PostgreSQL connection URL from config."""
        config = self.config
        password = os.getenv("FANSLY_PG_PASSWORD")
        if password is None:
            password = config.pg_password if config.pg_password is not None else ""
        password_encoded = quote_plus(password)
        return f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"

    async def create_entity_store(self) -> PostgresEntityStore:
        """Create and initialize the EntityStore with an asyncpg pool.

        This creates a direct asyncpg connection pool and wires it into
        the Pydantic models via FanslyObject._store.

        Returns:
            Configured PostgresEntityStore instance
        """
        if self._entity_store is not None:
            return self._entity_store

        config = self.config
        password = os.getenv("FANSLY_PG_PASSWORD")
        if password is None:
            password = config.pg_password if config.pg_password is not None else ""

        self._asyncpg_pool = await asyncpg.create_pool(
            host=config.pg_host,
            port=int(config.pg_port),
            database=config.pg_database,
            user=config.pg_user,
            password=password,
            min_size=2,
            max_size=config.pg_pool_size,
            command_timeout=30,
            init=PostgresEntityStore._init_pg_connection,
        )

        self._entity_store = PostgresEntityStore(self._asyncpg_pool)
        self._entity_store.register_models()

        # Preload all entities into identity map so model_validate merges
        # API data with DB state (preserves is_downloaded, content_hash, etc.)
        from .models import (
            Account,
            AccountMedia,
            AccountMediaBundle,
            Attachment,
            Group,
            Hashtag,
            Media,
            MediaStoryState,
            Message,
            Post,
            PostMention,
            Story,
            TimelineStats,
            Wall,
        )

        await self._entity_store.preload(
            [
                # Leaf entities first (no FK dependencies)
                Hashtag,
                Story,
                TimelineStats,
                MediaStoryState,
                # Media tree (Media before AccountMedia/Bundle)
                Media,
                AccountMedia,
                AccountMediaBundle,
                # Account before entities that reference it (Group.users, Post.accountId)
                Account,
                # Content entities
                Wall,
                Attachment,
                PostMention,
                Post,
                Message,
                Group,
            ]
        )

        db_logger.info("EntityStore initialized with asyncpg pool + preloaded")
        return self._entity_store

    @property
    def entity_store(self) -> PostgresEntityStore:
        """Access the EntityStore. Must call create_entity_store() first."""
        if self._entity_store is None:
            raise RuntimeError(
                "EntityStore not initialized. "
                "Call await db.create_entity_store() first."
            )
        return self._entity_store

    def _run_migrations(self) -> None:
        """Run Alembic migrations using the sync SA engine."""
        try:
            db_logger.info("Checking database migrations...")
            alembic_cfg = AlembicConfig("alembic.ini")

            with self._sync_engine.begin() as conn:
                alembic_cfg.attributes["connection"] = conn
                db_logger.info("Running database migrations...")
                alembic_upgrade(alembic_cfg, "head")

            db_logger.info("Migrations completed successfully")
        except Exception as e:
            db_logger.error(f"Error running migrations: {e}")
            raise

    async def cleanup(self) -> None:
        """Clean up all database connections.

        Thread-safe and idempotent.
        """
        if self._cleanup_done.is_set():
            return

        with self._cleanup_lock:
            if self._cleanup_done.is_set():
                return

            db_logger.info("Starting database cleanup...")

            try:
                # Close EntityStore
                if self._entity_store is not None:
                    try:
                        await self._entity_store.close()
                        db_logger.info("EntityStore closed")
                    except Exception as e:
                        db_logger.error(f"Error closing EntityStore: {e}")

                # Close asyncpg pool
                if self._asyncpg_pool is not None:
                    try:
                        await self._asyncpg_pool.close()
                        db_logger.info("asyncpg pool closed")
                    except Exception as e:
                        db_logger.error(f"Error closing asyncpg pool: {e}")

                # Dispose sync engine
                if self._sync_engine is not None:
                    try:
                        self._sync_engine.dispose()
                        db_logger.info("Sync engine disposed")
                    except Exception as e:
                        db_logger.error(f"Error disposing sync engine: {e}")

            except Exception as e:
                db_logger.error(f"Error during cleanup: {e}")
            finally:
                self._cleanup_done.set()
                db_logger.info("Database cleanup complete")

    def close_sync(self) -> None:
        """Synchronous cleanup for atexit handler."""
        if self._cleanup_done.is_set():
            return

        with self._cleanup_lock:
            if self._cleanup_done.is_set():
                return

            try:
                if self._sync_engine is not None:
                    self._sync_engine.dispose()
            except Exception as e:
                db_logger.error(f"Error during sync cleanup: {e}")
            finally:
                self._cleanup_done.set()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close_sync()
