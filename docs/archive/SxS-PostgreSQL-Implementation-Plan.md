# PostgreSQL Implementation Plan

## Overview

This document outlines the plan to add PostgreSQL support to the metadata database system while maintaining compatibility with the existing SQLite implementation.

## Current Architecture Analysis

### SQLite Implementation

The current `Database` class (in `metadata/database.py`) uses:

1. **Per-Creator Isolation via Separate Files**:

   - When `separate_metadata=True`: Creates separate SQLite files per creator
   - File naming: `{creator_name}_metadata.sqlite3`
   - Each creator gets completely isolated database

2. **In-Memory with Write-Through**:

   - Loads SQLite file into shared memory
   - Background thread syncs to disk periodically
   - Optimized for performance with minimal I/O

3. **Key Features**:
   - Shared memory URIs for multi-thread access
   - Prepared statement caching
   - Both sync and async session support
   - Alembic migrations

## PostgreSQL Schema Isolation Strategy

### Why Schemas Instead of Databases?

PostgreSQL supports three levels of isolation:

1. **Separate Databases**: Overkill, requires separate connections, harder to manage
2. **Schemas**: Perfect balance - logical isolation within one database
3. **Table Prefixes**: Pollutes namespace, complicates queries

**Choice**: Use PostgreSQL **schemas** for per-creator isolation.

### Schema Naming Convention

```python
# Global metadata (when separate_metadata=False)
schema_name = "public"  # Default PostgreSQL schema

# Per-creator metadata (when separate_metadata=True)
schema_name = f"creator_{safe_creator_name}"
# Example: "creator_alicewonder", "creator_bob_smith"
```

### SQLAlchemy Schema Support

SQLAlchemy 2.x has excellent schema support:

```python
from sqlalchemy import MetaData

# Create metadata with schema
metadata = MetaData(schema="creator_alice")

# All tables in this metadata will be in that schema
# Example: creator_alice.accounts, creator_alice.media, etc.
```

## Design

### 1. Database Type Enum

```python
# config/database_type.py
from enum import Enum

class DatabaseType(Enum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
```

### 2. Configuration Additions

Add to `FanslyConfig`:

```python
@dataclass
class FanslyConfig(PathConfig):
    # Existing SQLite fields
    metadata_db_file: Path | None = None
    separate_metadata: bool = False
    db_sync_commits: int | None = None
    db_sync_seconds: int | None = None

    # New PostgreSQL fields
    db_type: DatabaseType = DatabaseType.SQLITE  # Default to SQLite
    pg_host: str | None = None
    pg_port: int = 5432
    pg_database: str | None = None
    pg_user: str | None = None
    pg_password: str | None = None
    pg_schema: str | None = None  # Optional: override auto-generated schema name

    # SSL/TLS options
    pg_sslmode: str = "prefer"  # prefer, require, disable
    pg_sslcert: Path | None = None
    pg_sslkey: Path | None = None
    pg_sslrootcert: Path | None = None
```

### 3. Abstract Database Interface

Create a common interface that both SQLite and PostgreSQL implementations follow:

```python
# metadata/database_interface.py
from abc import ABC, abstractmethod
from contextlib import contextmanager, asynccontextmanager
from typing import Generator, AsyncGenerator
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

class DatabaseInterface(ABC):
    """Abstract interface for database implementations."""

    def __init__(self, config: FanslyConfig, *, creator_name: str | None = None):
        """Initialize database.

        Args:
            config: Configuration object
            creator_name: Optional creator name for isolated metadata
        """
        pass

    @abstractmethod
    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Get a sync session with transaction management."""
        pass

    @abstractmethod
    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession]:
        """Get an async session with transaction management."""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up database connections and resources."""
        pass

    @abstractmethod
    def close_sync(self) -> None:
        """Synchronous cleanup for atexit handlers."""
        pass
```

### 4. PostgreSQL Database Implementation

```python
# metadata/database_postgres.py
from sqlalchemy import create_engine, event, text, MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from .database_interface import DatabaseInterface

class PostgreSQLDatabase(DatabaseInterface):
    """PostgreSQL implementation with schema isolation."""

    def __init__(self, config: FanslyConfig, *, creator_name: str | None = None):
        """Initialize PostgreSQL database.

        When separate_metadata=True and creator_name is provided,
        uses schema isolation: creator_{safe_name}

        When separate_metadata=False or creator_name is None,
        uses the default 'public' schema.
        """
        self.config = config
        self.creator_name = creator_name

        # Determine schema name
        if creator_name and config.separate_metadata:
            safe_name = "".join(c if c.isalnum() else "_" for c in creator_name.lower())
            self.schema_name = f"creator_{safe_name}"
        else:
            self.schema_name = config.pg_schema or "public"

        # Build connection URL
        self.db_url = self._build_connection_url()
        self.async_db_url = self.db_url.replace("postgresql://", "postgresql+asyncpg://")

        # Create engines
        self._sync_engine = create_engine(
            self.db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )

        self._async_engine = create_async_engine(
            self.async_db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )

        # Set up schema
        self._setup_schema()

        # Run migrations with schema
        self._run_migrations()

        # Create session factories
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            expire_on_commit=False,
        )

        self._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    def _build_connection_url(self) -> str:
        """Build PostgreSQL connection URL from config."""
        user = self.config.pg_user
        password = self.config.pg_password
        host = self.config.pg_host
        port = self.config.pg_port
        database = self.config.pg_database

        # Build URL with proper escaping
        from urllib.parse import quote_plus

        if password:
            password_encoded = quote_plus(password)
            auth = f"{user}:{password_encoded}"
        else:
            auth = user

        url = f"postgresql://{auth}@{host}:{port}/{database}"

        # Add SSL parameters
        if self.config.pg_sslmode != "disable":
            url += f"?sslmode={self.config.pg_sslmode}"
            if self.config.pg_sslcert:
                url += f"&sslcert={self.config.pg_sslcert}"
            if self.config.pg_sslkey:
                url += f"&sslkey={self.config.pg_sslkey}"
            if self.config.pg_sslrootcert:
                url += f"&sslrootcert={self.config.pg_sslrootcert}"

        return url

    def _setup_schema(self) -> None:
        """Create schema if it doesn't exist."""
        with self._sync_engine.connect() as conn:
            # Create schema if needed (skip for 'public')
            if self.schema_name != "public":
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}"))
                conn.commit()

            # Set search_path for this connection
            conn.execute(text(f"SET search_path TO {self.schema_name}, public"))
            conn.commit()

    def _run_migrations(self) -> None:
        """Run Alembic migrations in the schema."""
        from alembic.config import Config as AlembicConfig
        from alembic.command import upgrade as alembic_upgrade

        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", self.db_url)

        # Set the schema for migrations
        with self._sync_engine.connect() as conn:
            # Set search_path before running migrations
            conn.execute(text(f"SET search_path TO {self.schema_name}, public"))

            # Run migrations in this schema
            alembic_cfg.attributes["connection"] = conn
            with conn.begin():
                alembic_upgrade(alembic_cfg, "head")

    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Get a sync session with schema set."""
        session = self._sync_session_factory()

        try:
            # Set search_path for this session
            session.execute(text(f"SET search_path TO {self.schema_name}, public"))

            yield session

            if session.is_active:
                session.commit()
        except Exception:
            if session.is_active:
                session.rollback()
            raise
        finally:
            session.close()

    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession]:
        """Get an async session with schema set."""
        session = self._async_session_factory()

        try:
            # Set search_path for this session
            await session.execute(text(f"SET search_path TO {self.schema_name}, public"))

            yield session

            if session.in_transaction() and session.is_active:
                await session.commit()
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise
        finally:
            await session.close()

    async def cleanup(self) -> None:
        """Clean up PostgreSQL connections."""
        if self._async_engine:
            await self._async_engine.dispose()
        if self._sync_engine:
            self._sync_engine.dispose()

    def close_sync(self) -> None:
        """Synchronous cleanup."""
        if self._sync_engine:
            self._sync_engine.dispose()
```

### 5. Database Factory

Create a factory to instantiate the correct database implementation:

```python
# metadata/database_factory.py
from config import FanslyConfig, DatabaseType
from .database_interface import DatabaseInterface
from .database import Database as SQLiteDatabase
from .database_postgres import PostgreSQLDatabase

def create_database(
    config: FanslyConfig,
    *,
    creator_name: str | None = None,
    skip_migrations: bool = False,
) -> DatabaseInterface:
    """Create the appropriate database implementation.

    Args:
        config: Configuration object
        creator_name: Optional creator name for isolated metadata
        skip_migrations: Skip running migrations during initialization

    Returns:
        Database implementation (SQLite or PostgreSQL)
    """
    if config.db_type == DatabaseType.SQLITE:
        return SQLiteDatabase(
            config,
            creator_name=creator_name,
            skip_migrations=skip_migrations,
        )
    elif config.db_type == DatabaseType.POSTGRESQL:
        return PostgreSQLDatabase(
            config,
            creator_name=creator_name,
            skip_migrations=skip_migrations,
        )
    else:
        raise ValueError(f"Unsupported database type: {config.db_type}")
```

### 6. Model Metadata Management

The tricky part: SQLAlchemy models need to be schema-aware.

**Option A: Dynamic MetaData** (Recommended)

```python
# metadata/models/__init__.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """Base class for all models."""

    # Don't set metadata here - it will be set dynamically
    metadata: MetaData

    @classmethod
    def set_schema(cls, schema_name: str | None = None):
        """Set the schema for all tables.

        This must be called before any tables are created.
        """
        if schema_name:
            cls.metadata = MetaData(schema=schema_name)
        else:
            cls.metadata = MetaData()
```

**Usage in Database Implementation**:

```python
def _setup_schema(self) -> None:
    """Create schema and configure table metadata."""
    # Import models (they're registered with Base)
    from metadata import models  # This imports all models
    from metadata.models import Base

    # Set schema on all models
    Base.set_schema(self.schema_name if self.schema_name != "public" else None)

    # Now create schema
    with self._sync_engine.connect() as conn:
        if self.schema_name != "public":
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}"))
            conn.commit()
```

**Option B: Connection-Level search_path** (Simpler)

Always set `search_path` on every connection/session. This way the models don't need schema names, PostgreSQL handles routing automatically.

```python
@event.listens_for(Engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    """Set search_path on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute(f"SET search_path TO {schema_name}, public")
    cursor.close()
```

**Recommendation**: Use **Option B** (connection-level search_path) because:

- No changes needed to existing models
- Simpler implementation
- More compatible with Alembic
- Works automatically with all queries

## Migration Strategy

### Alembic Configuration

Update `alembic/env.py` to be schema-aware:

```python
def run_migrations_online():
    """Run migrations in 'online' mode."""
    # Get schema from config or environment
    schema_name = os.getenv("FANSLY_DB_SCHEMA", "public")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Set search_path before running migrations
        connection.execute(text(f"SET search_path TO {schema_name}, public"))

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=schema_name if schema_name != "public" else None,
        )

        with context.begin_transaction():
            context.run_migrations()
```

### Schema-Specific Alembic Version Tables

Each schema should have its own `alembic_version` table:

```sql
-- For creator_alice schema
creator_alice.alembic_version

-- For creator_bob schema
creator_bob.alembic_version

-- For public schema (global)
public.alembic_version
```

This ensures each creator's schema can have independent migration versions.

## Configuration File Example

```ini
[Options]
# Database type: sqlite or postgresql
db_type = postgresql

# SQLite options (when db_type=sqlite)
metadata_db_file = /path/to/metadata_db.sqlite3
separate_metadata = true
db_sync_commits = 1000
db_sync_seconds = 60

# PostgreSQL options (when db_type=postgresql)
pg_host = localhost
pg_port = 5432
pg_database = fansly_metadata
pg_user = fansly_user
pg_password = secret_password
pg_schema = public
pg_sslmode = prefer

# Schema isolation (works for both SQLite and PostgreSQL)
separate_metadata = true
```

## Implementation Steps

1. **Phase 1: Foundation**

   - [ ] Add `DatabaseType` enum
   - [ ] Add PostgreSQL config fields to `FanslyConfig`
   - [ ] Create `DatabaseInterface` abstract class
   - [ ] Create `database_factory.py`

2. **Phase 2: PostgreSQL Implementation**

   - [ ] Implement `PostgreSQLDatabase` class
   - [ ] Add schema creation logic
   - [ ] Add connection pooling
   - [ ] Set up search_path handling

3. **Phase 3: Migrations**

   - [ ] Update `alembic/env.py` for schema awareness
   - [ ] Test migrations in different schemas
   - [ ] Add migration rollback support

4. **Phase 4: Refactoring**

   - [ ] Refactor existing `Database` to implement `DatabaseInterface`
   - [ ] Update all database instantiation to use factory
   - [ ] Remove SQLite-specific code from shared paths

5. **Phase 5: Testing**

   - [ ] Unit tests for schema isolation
   - [ ] Integration tests with both databases
   - [ ] Performance comparison tests
   - [ ] Migration tests

6. **Phase 6: Documentation**
   - [ ] Update README with PostgreSQL setup
   - [ ] Document configuration options
   - [ ] Create migration guide from SQLite to PostgreSQL
   - [ ] Add troubleshooting guide

## Benefits of This Approach

### 1. Schema Isolation (PostgreSQL)

- Each creator gets their own schema
- No name conflicts between creators
- Easy to manage per-creator data
- Can set per-schema permissions

### 2. Backward Compatibility

- SQLite continues to work exactly as before
- No breaking changes for existing users
- Gradual migration path

### 3. SQLAlchemy Compatibility

- Minimal changes to models
- Uses standard SQLAlchemy features
- Works with Alembic migrations

### 4. Performance

- PostgreSQL connection pooling
- No in-memory sync overhead (PostgreSQL handles this)
- Better for concurrent access
- More scalable for many creators

### 5. Production Ready

- SSL/TLS support
- Connection retry logic
- Proper transaction handling
- Schema-level isolation

## Potential Challenges and Solutions

### Challenge 1: Alembic with Schemas

**Problem**: Alembic needs to know which schema to use for migrations.

**Solution**:

- Pass schema via environment variable
- Set `version_table_schema` in Alembic context
- Each schema gets its own `alembic_version` table

### Challenge 2: Cross-Schema Queries

**Problem**: What if we need to query across creators?

**Solution**:

- Use qualified table names: `creator_alice.posts`
- Set search_path with multiple schemas: `SET search_path TO creator_alice, creator_bob, public`
- Create views in public schema for cross-creator queries

### Challenge 3: Model Metadata

**Problem**: SQLAlchemy models are defined once but need different schemas.

**Solution**:

- Use connection-level `search_path` (recommended)
- PostgreSQL automatically routes queries to correct schema
- Models don't need schema names in definition

### Challenge 4: Testing

**Problem**: Need to test both SQLite and PostgreSQL.

**Solution**:

- Use pytest fixtures to parameterize tests
- Run test suite against both database types
- Use TestContainers for PostgreSQL in CI/CD

## Example Usage

```python
from config import FanslyConfig, DatabaseType
from metadata.database_factory import create_database

# SQLite (existing behavior)
config = FanslyConfig(
    program_version="1.0.0",
    db_type=DatabaseType.SQLITE,
    separate_metadata=True,
)
db = create_database(config, creator_name="alice")

# PostgreSQL with schema isolation
config = FanslyConfig(
    program_version="1.0.0",
    db_type=DatabaseType.POSTGRESQL,
    pg_host="localhost",
    pg_database="fansly_metadata",
    pg_user="fansly_user",
    pg_password="secret",
    separate_metadata=True,
)
db = create_database(config, creator_name="alice")
# Uses schema: creator_alice

# Both work the same way
async with db.async_session_scope() as session:
    result = await session.execute(select(Account))
    accounts = result.scalars().all()
```

## Security Considerations

1. **Password Storage**: Never commit passwords to config
2. **SSL/TLS**: Use SSL for production PostgreSQL
3. **Schema Permissions**: Set appropriate PostgreSQL permissions
4. **SQL Injection**: SQLAlchemy handles parameterization, but validate schema names
5. **Connection Strings**: Use environment variables for sensitive data

## Performance Considerations

1. **Connection Pooling**: PostgreSQL uses connection pools efficiently
2. **No Sync Overhead**: No need for background sync thread
3. **Indexes**: Same index strategy works for both databases
4. **Query Performance**: PostgreSQL query optimizer is excellent
5. **Concurrent Access**: PostgreSQL handles concurrent writes better than SQLite

## Conclusion

This design provides a clean path to add PostgreSQL support while:

- Maintaining full backward compatibility with SQLite
- Using PostgreSQL schemas for per-creator isolation
- Following SQLAlchemy best practices
- Keeping models unchanged
- Supporting both sync and async operations

The schema-based isolation is equivalent to SQLite's file-based isolation but more efficient for a shared database instance.
