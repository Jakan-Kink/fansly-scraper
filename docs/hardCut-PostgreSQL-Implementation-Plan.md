# Hard Cutover: PostgreSQL Implementation Plan

## Overview

This plan outlines a **complete replacement** of SQLite with PostgreSQL for the metadata database. No dual-support, no abstraction layers - just a clean cutover.

## Why PostgreSQL?

1. **Better concurrent access** - No file locking issues
2. **Schema isolation** - Cleaner than separate files
3. **Network storage** - Works better over network paths
4. **Mature ecosystem** - Better tooling and monitoring
5. **Scalability** - Handles many creators efficiently

## Current SQLite Architecture (To Be Removed)

The current implementation has significant complexity:
- In-memory database with write-through caching
- Background sync thread to persist to disk
- Shared memory URIs for multi-thread access
- Complex cleanup and finalization
- ~1800 lines in `database.py`

**All of this goes away with PostgreSQL.**

## PostgreSQL Architecture (New)

### Schema-Based Isolation

Instead of separate SQLite files per creator:

```
SQLite (old):
├── alice_metadata.sqlite3
├── bob_metadata.sqlite3
└── charlie_metadata.sqlite3

PostgreSQL (new):
fansly_metadata (one database)
├── creator_alice (schema)
│   ├── accounts
│   ├── media
│   ├── posts
│   └── alembic_version
├── creator_bob (schema)
│   ├── accounts
│   ├── media
│   └── ...
└── public (schema - for global metadata)
    ├── accounts
    ├── media
    └── ...
```

### Connection String

```python
# When separate_metadata=False (global)
postgresql://user:pass@host:port/fansly_metadata
# Uses schema: public

# When separate_metadata=True, creator="alice"
postgresql://user:pass@host:port/fansly_metadata
# Uses schema: creator_alice
```

## Implementation Changes

### 1. Configuration Updates

**Add to `config/fanslyconfig.py`:**

```python
@dataclass
class FanslyConfig(PathConfig):
    # Remove SQLite-specific fields (keep for migration reference)
    # metadata_db_file: Path | None = None  # DELETE after migration
    # db_sync_commits: int | None = None    # DELETE (not needed for PostgreSQL)
    # db_sync_seconds: int | None = None    # DELETE (not needed for PostgreSQL)
    # db_sync_min_size: int | None = None   # DELETE (not needed for PostgreSQL)

    # Keep this - works for both SQLite and PostgreSQL
    separate_metadata: bool = False

    # PostgreSQL configuration (NEW)
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "fansly_metadata"
    pg_user: str = "fansly_user"
    pg_password: str | None = None

    # Optional schema override (defaults to auto-generated)
    pg_schema: str | None = None

    # SSL/TLS settings
    pg_sslmode: str = "prefer"  # disable, allow, prefer, require, verify-ca, verify-full
    pg_sslcert: Path | None = None
    pg_sslkey: Path | None = None
    pg_sslrootcert: Path | None = None

    # Connection pool settings
    pg_pool_size: int = 5
    pg_max_overflow: int = 10
    pg_pool_timeout: int = 30
```

**Add to `config/args.py`:**

```python
parser.add_argument(
    "--pg-host",
    dest="pg_host",
    help="PostgreSQL host (default: localhost)",
)
parser.add_argument(
    "--pg-port",
    type=int,
    dest="pg_port",
    help="PostgreSQL port (default: 5432)",
)
parser.add_argument(
    "--pg-database",
    dest="pg_database",
    help="PostgreSQL database name",
)
parser.add_argument(
    "--pg-user",
    dest="pg_user",
    help="PostgreSQL username",
)
parser.add_argument(
    "--pg-password",
    dest="pg_password",
    help="PostgreSQL password (prefer environment variable)",
)
```

**Update `config/config.py`:**

```python
# PostgreSQL configuration with environment variable fallback
config.pg_host = config._parser.get(section, "pg_host", fallback="localhost")
config.pg_port = config._parser.getint(section, "pg_port", fallback=5432)
config.pg_database = config._parser.get(section, "pg_database", fallback="fansly_metadata")
config.pg_user = config._parser.get(section, "pg_user", fallback="fansly_user")

# Password from env var or config (prefer env var)
config.pg_password = os.getenv("FANSLY_PG_PASSWORD") or config._parser.get(
    section, "pg_password", fallback=None
)
```

### 2. Database Class Refactor

**File: `metadata/database.py`**

Major simplifications:

#### Remove (DELETE):
- `_sync_to_disk()` method (~300 lines)
- `_sync_task()` method (~100 lines)
- `_sync_thread` and all thread management
- `_shared_connection` and shared memory URIs
- `_prepare_statements()` - PostgreSQL has better prepared statements
- `_configure_memory_settings()` - SQLite-specific
- All SQLite-specific imports (`sqlite3`, `shutil`, etc.)

#### Keep (MODIFY):
- `__init__()` - simplified significantly
- `session_scope()` - minor changes
- `async_session_scope()` - minor changes
- `cleanup()` - much simpler
- `close_sync()` - much simpler

#### Add (NEW):
- `_build_connection_url()` - construct PostgreSQL URL
- `_setup_schema()` - create schema if needed
- `_set_search_path()` - event listener for connections

#### New `__init__` structure:

```python
def __init__(
    self,
    config: FanslyConfig,
    *,
    creator_name: str | None = None,
    skip_migrations: bool = False,
) -> None:
    """Initialize PostgreSQL database manager.

    Args:
        config: FanslyConfig instance
        creator_name: Optional creator name for schema isolation
        skip_migrations: Skip running migrations during initialization
    """
    self.config = config
    self.creator_name = creator_name

    # Determine schema name
    if creator_name and config.separate_metadata:
        safe_name = "".join(c if c.isalnum() else "_" for c in creator_name.lower())
        self.schema_name = f"creator_{safe_name}"
    else:
        self.schema_name = config.pg_schema or "public"

    # Build connection URLs
    self.db_url = self._build_connection_url()
    self.async_db_url = self.db_url.replace("postgresql://", "postgresql+asyncpg://")

    # Create engines (much simpler than SQLite!)
    self._sync_engine = create_engine(
        self.db_url,
        pool_size=config.pg_pool_size,
        max_overflow=config.pg_max_overflow,
        pool_timeout=config.pg_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

    self._async_engine = create_async_engine(
        self.async_db_url,
        pool_size=config.pg_pool_size,
        max_overflow=config.pg_max_overflow,
        pool_timeout=config.pg_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

    # Set search_path on all connections
    event.listen(self._sync_engine, "connect", self._set_search_path)
    event.listen(self._async_engine.sync_engine, "connect", self._set_search_path)

    # Create schema if needed
    self._setup_schema()

    # Run migrations
    if not skip_migrations:
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
```

### 3. Alembic Updates

**File: `alembic/env.py`**

Add schema support:

```python
def run_migrations_online():
    """Run migrations in 'online' mode."""
    # Get schema from environment variable
    schema_name = os.getenv("FANSLY_DB_SCHEMA", "public")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Set search_path before running migrations
        connection.execute(text(f"SET search_path TO {schema_name}, public"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Each schema has its own version table
            version_table_schema=schema_name if schema_name != "public" else None,
        )

        with context.begin_transaction():
            context.run_migrations()
```

### 4. Dependencies Update

**File: `pyproject.toml`**

```toml
[tool.poetry.dependencies]
# Remove: aiosqlite (SQLite async driver)

# Add PostgreSQL drivers
asyncpg = "^0.29.0"  # Async PostgreSQL driver
psycopg2-binary = "^2.9.9"  # Sync PostgreSQL driver

# Keep existing
sqlalchemy = "^2.0.0"
alembic = "^1.13.0"
```

### 5. Code Complexity Reduction

**Before (SQLite):**
- `database.py`: ~1800 lines
- Background sync thread
- Complex cleanup coordination
- In-memory database management
- File I/O and atomic writes

**After (PostgreSQL):**
- `database.py`: ~400 lines
- No background threads
- Simple cleanup
- No file management
- Native PostgreSQL features

**Reduction: ~75% less code**

## Migration Strategy

### For Existing Users

Provide a migration script `scripts/migrate_to_postgres.py`:

```bash
# Migrate all SQLite databases to PostgreSQL
python scripts/migrate_to_postgres.py \
    --sqlite-path /path/to/metadata \
    --pg-host localhost \
    --pg-database fansly_metadata \
    --pg-user fansly_user

# Migrate specific creator
python scripts/migrate_to_postgres.py \
    --sqlite-file /path/to/alice_metadata.sqlite3 \
    --creator-name alice \
    --pg-host localhost
```

The script will:
1. Read all tables from SQLite
2. Create PostgreSQL schema
3. Copy all data preserving IDs and relationships
4. Verify data integrity
5. Create backup of SQLite before deletion

### Configuration Migration

Update `config.ini`:

```ini
[Options]
# Old (remove after migration)
# metadata_db_file = /path/to/metadata_db.sqlite3
# db_sync_commits = 1000
# db_sync_seconds = 60

# New
pg_host = localhost
pg_port = 5432
pg_database = fansly_metadata
pg_user = fansly_user
# pg_password =  # Use environment variable instead!

# This setting works the same
separate_metadata = true
```

Environment variable for password:
```bash
export FANSLY_PG_PASSWORD=your_secure_password
```

## PostgreSQL Setup

### Initial Database Setup

```sql
-- Create database
CREATE DATABASE fansly_metadata
    WITH ENCODING = 'UTF8'
         LC_COLLATE = 'en_US.UTF-8'
         LC_CTYPE = 'en_US.UTF-8';

-- Create user
CREATE USER fansly_user WITH PASSWORD 'your_secure_password';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE fansly_metadata TO fansly_user;

-- Connect to database
\c fansly_metadata

-- Grant schema creation rights
GRANT CREATE ON DATABASE fansly_metadata TO fansly_user;

-- For existing public schema
GRANT ALL ON SCHEMA public TO fansly_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO fansly_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO fansly_user;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO fansly_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON SEQUENCES TO fansly_user;
```

### Docker Compose Setup (Optional)

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: fansly_metadata
      POSTGRES_USER: fansly_user
      POSTGRES_PASSWORD: your_secure_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init-db.sql:/docker-entrypoint-initdb.d/init.sql
    restart: unless-stopped

volumes:
  postgres_data:
```

## Implementation Checklist

### Phase 1: Preparation
- [ ] Add PostgreSQL dependencies to `pyproject.toml`
- [ ] Add PostgreSQL config fields to `FanslyConfig`
- [ ] Add command-line args for PostgreSQL
- [ ] Update config file parsing

### Phase 2: Database Class Refactor
- [ ] Create `_build_connection_url()` method
- [ ] Create `_setup_schema()` method
- [ ] Create `_set_search_path()` event listener
- [ ] Simplify `__init__()` - remove SQLite code
- [ ] Update `session_scope()` for PostgreSQL
- [ ] Update `async_session_scope()` for PostgreSQL
- [ ] Simplify `cleanup()` - no sync thread
- [ ] Simplify `close_sync()` - just dispose engines
- [ ] Remove all SQLite-specific methods

### Phase 3: Alembic Updates
- [ ] Update `alembic/env.py` for schema support
- [ ] Test migrations in different schemas
- [ ] Update migration documentation

### Phase 4: Migration Script
- [ ] Write `scripts/migrate_to_postgres.py`
- [ ] Add data validation
- [ ] Add progress reporting
- [ ] Add rollback capability

### Phase 5: Testing
- [ ] Test with single creator (public schema)
- [ ] Test with multiple creators (separate schemas)
- [ ] Test migrations
- [ ] Test concurrent access
- [ ] Performance testing

### Phase 6: Documentation
- [ ] Update README with PostgreSQL requirements
- [ ] Document configuration options
- [ ] Create migration guide
- [ ] Add troubleshooting section
- [ ] Update CLAUDE.md

## Benefits Summary

### Code Quality
- **-75% code** in `database.py`
- **No background threads** - simpler debugging
- **No file I/O** - cleaner architecture
- **Better error handling** - PostgreSQL errors are clearer

### Performance
- **Better concurrent writes** - no file locking
- **Connection pooling** - efficient reuse
- **No sync overhead** - writes go directly to DB
- **Query optimizer** - PostgreSQL is excellent

### Reliability
- **ACID guarantees** - native to PostgreSQL
- **No corruption** - no file system issues
- **Better backups** - use `pg_dump`
- **Point-in-time recovery** - built into PostgreSQL

### Scalability
- **Many creators** - schemas scale better than files
- **Network storage** - works great over network
- **Monitoring** - use standard PostgreSQL tools
- **Maintenance** - vacuum, analyze, etc.

## Risks and Mitigations

### Risk 1: User Setup Complexity
**Risk**: Users need to install and configure PostgreSQL

**Mitigation**:
- Provide Docker Compose setup
- Write detailed setup guide
- Include automated setup script
- Provide cloud PostgreSQL options (AWS RDS, etc.)

### Risk 2: Data Migration Issues
**Risk**: Migration script might fail or corrupt data

**Mitigation**:
- Migration script creates SQLite backup first
- Validate data after migration
- Provide rollback procedure
- Test extensively before release

### Risk 3: Performance Regression
**Risk**: PostgreSQL might be slower for small databases

**Mitigation**:
- Run performance benchmarks
- Tune PostgreSQL configuration
- Use connection pooling
- PostgreSQL is generally faster for concurrent access

### Risk 4: Breaking Changes
**Risk**: Existing installations break on update

**Mitigation**:
- Detect SQLite on startup
- Prompt to run migration
- Keep migration script maintained
- Version number bump to indicate breaking change

## Timeline

- **Day 1**: Configuration and dependencies
- **Day 2**: Database class refactor
- **Day 3**: Alembic updates and testing
- **Day 4**: Migration script development
- **Day 5**: Testing and documentation
- **Day 6**: Final testing and release prep

**Total: 1 week** for complete implementation and testing

## Rollback Plan

If PostgreSQL doesn't work out:

1. Keep SQLite code in a branch
2. Tag the last SQLite version
3. Allow users to downgrade
4. Provide reverse migration (PostgreSQL → SQLite)

## Conclusion

Hard cutover to PostgreSQL is the right choice:

- **Simpler implementation** (1 week vs 3 weeks)
- **Better architecture** (no background threads)
- **More maintainable** (75% less code)
- **More scalable** (schemas vs files)
- **Industry standard** (PostgreSQL is proven)

The complexity of supporting both databases is not worth the effort when PostgreSQL is superior for this use case.
