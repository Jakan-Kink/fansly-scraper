# Metadata Test Migration Guide

**Goal**: Ensure all metadata tests use real database sessions and factory patterns, not mocked objects.

**Created**: 2025-11-18
**Reference**: `STASH_TEST_MIGRATION_TODO.md` for migration philosophy

---

## Overview

### Current State: Excellent

The metadata tests are in **excellent condition**. The vast majority already follow best practices using:

- Real PostgreSQL database sessions (UUID-isolated per test)
- FactoryBoy factories for creating test data
- Proper async session handling

### Summary Statistics

| Metric                         | Count                                                         |
| ------------------------------ | ------------------------------------------------------------- |
| Total test files               | 14                                                            |
| Total test functions           | ~150+                                                         |
| Tests following best practices | ~145                                                          |
| Tests needing migration        | ~5                                                            |
| Estimated migration effort     | **45 min - 1 hour** (reduced from 2-4 hours with spy pattern) |

---

## Test Categories

### Category A: Fully Compliant (No Work Needed)

These files already use real sessions and factories:

| File                                         | Functions | Status |
| -------------------------------------------- | --------- | ------ |
| `tests/metadata/test_account.py`             | ~15       | ✅     |
| `tests/metadata/test_media.py`               | ~20       | ✅     |
| `tests/metadata/test_post.py`                | ~15       | ✅     |
| `tests/metadata/test_message.py`             | ~18       | ✅     |
| `tests/metadata/test_attachment.py`          | ~12       | ✅     |
| `tests/metadata/test_wall.py`                | ~10       | ✅     |
| `tests/metadata/test_story.py`               | ~8        | ✅     |
| `tests/metadata/test_hashtag.py`             | ~6        | ✅     |
| `tests/metadata/test_stub_tracker.py`        | ~8        | ✅     |
| `tests/metadata/test_base.py`                | ~10       | ✅     |
| `tests/metadata/test_decorators.py`          | ~5        | ✅     |
| `tests/metadata/test_media_utils.py`         | ~8        | ✅     |
| `tests/metadata/test_relationship_logger.py` | ~5        | ✅     |

### Category B: Needs Migration

| File                              | Violations | Status |
| --------------------------------- | ---------- | ------ |
| `tests/metadata/test_database.py` | ~5         | ❌     |

---

## External Boundaries for Metadata

The true external boundaries that CAN be mocked:

1. **File I/O**: Reading/writing files to disk
2. **External Libraries**: `imagehash.phash()`, `hashlib` for content hashing
3. **Network Operations**: If any HTTP calls exist (rare in metadata)

**Do NOT mock**:

- SQLAlchemy sessions or queries
- Model factory methods
- Database models themselves
- Internal processing functions

---

## Migration Patterns

### Pattern 1: Replace MagicMock Models with Factories

```python
# ❌ WRONG: MagicMock for database models
def test_something():
    mock_account = MagicMock(spec=Account)
    mock_account.id = 12345
    mock_account.username = "test"

# ✅ CORRECT: Use factory
def test_something(session_sync):
    account = AccountFactory.build(id=12345, username="test")
    session_sync.add(account)
    session_sync.commit()
```

### Pattern 2: Replace Mocked Sessions with Real Sessions

```python
# ❌ WRONG: Mocked async session
async def test_query():
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.return_value.scalar_one_or_none.return_value = None

# ✅ CORRECT: Real session from fixture
async def test_query(session):
    # Use real session - it's already connected to isolated test database
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one_or_none()
    assert account is None
```

### Pattern 3: Test Real Database Constraints

```python
# ❌ WRONG: Test passes because mock doesn't enforce constraints
def test_relationship():
    mock_media = MagicMock()
    mock_media.account = MagicMock()  # No real FK constraint!

# ✅ CORRECT: Real objects test real constraints
def test_relationship(session_sync):
    account = AccountFactory.build(id=12345)
    session_sync.add(account)
    session_sync.commit()

    media = MediaFactory.build(id=67890, accountId=12345)
    session_sync.add(media)
    session_sync.commit()

    # Refresh to load relationship
    session_sync.refresh(media)
    assert media.account.id == 12345
```

### Pattern 4: Spy Pattern for Internal Orchestration Tests

For tests that verify internal method coordination (e.g., early returns, call counts, error propagation):

```python
# ❌ WRONG: Mocking internal method with return_value/side_effect
async def test_process_with_early_return():
    with patch.object(processor, "_validate_data", AsyncMock(return_value=False)):
        result = await processor.process_items([item1, item2, item3])
        # This never tests real validation logic!

# ✅ CORRECT: Spy pattern with wraps - real code executes
async def test_process_with_early_return(session):
    original_validate = processor._validate_data
    call_count = 0
    validation_results = []

    async def spy_validate(item):
        nonlocal call_count
        call_count += 1
        result = await original_validate(item)  # Real code executes!
        validation_results.append((item.id, result))
        return result

    with patch.object(processor, "_validate_data", wraps=spy_validate):
        result = await processor.process_items([item1, item2, item3])

        # Verify orchestration
        assert call_count == 3  # All items validated
        assert validation_results[0][1] is True  # First item passed
        assert validation_results[1][1] is False  # Second item failed
        # Verify early return behavior
        assert len(result.processed) == 1
        assert len(result.failed) == 2
```

**Why spy pattern matters:**

- Real code path executes (not mocked behavior)
- Tests actual orchestration logic (early returns, loops, error handling)
- Catches regressions in internal coordination
- Use sparingly - prefer external boundary mocking when possible
- Best for complex workflows with conditional logic

**When to use spy pattern:**

1. Testing early return conditions that depend on real data validation
2. Verifying call order/count in multi-step processing pipelines
3. Confirming error propagation through internal methods
4. Validating retry logic or exponential backoff

**When NOT to use spy pattern:**

1. If you can achieve the same test with external boundary mocks (prefer that)
2. Testing simple pass-through methods (no orchestration logic)
3. When the internal method has no conditional logic

---

## File-by-File Migration Guide

### `tests/metadata/test_database.py`

**Current Issues**:

- 5 URL building tests mock database engines to avoid connections
- 1 orchestration test mocks `_run_migrations` method

**Migration Steps**:

**Tests 1-5 (URL Building & Config)**: Use real PostgreSQL fixtures

1. Replace all mocks with real `FanslyConfig` from factory
2. Use `test_database` fixture for real isolated PostgreSQL
3. Remove all engine/logging mocks
4. Assert on `db.db_url`, `db.async_db_url`, `db.config`, `db.schema_name`

**Test 6 (Migration Skip Flag)**: Apply spy pattern

1. Wrap `Database._run_migrations` with spy function
2. Verify call count matches expected behavior (0 when skip=True, 1 when skip=False)
3. Real migration code still executes (not mocked)

**Example Migration - URL Building**:

```python
# ❌ BEFORE: Mocked database connection
def test_database_init():
    with patch('metadata.database.create_async_engine') as mock_engine:
        mock_engine.return_value = MagicMock()
        db = Database(connection_string="sqlite:///:memory:")
        mock_engine.assert_called_once()

# ✅ AFTER: Use real database fixture
async def test_build_connection_url_basic(test_database):
    """Test basic PostgreSQL URL construction."""
    db = Database(test_database.config, skip_migrations=True)
    url = db._build_connection_url()

    assert "postgresql://" in url
    assert f"{test_database.config.pg_user}:" in url
    assert f"@{test_database.config.pg_host}:{test_database.config.pg_port}" in url
    assert f"/{test_database.config.pg_database}" in url
```

**Example Migration - Orchestration (Spy Pattern)**:

```python
# ❌ BEFORE: Mocking internal method
def test_skip_migrations_flag():
    with patch.object(Database, "_run_migrations") as mock_run:
        db1 = Database(mock_config, skip_migrations=True)
        mock_run.assert_not_called()

        db2 = Database(mock_config, skip_migrations=False)
        mock_run.assert_called_once()

# ✅ AFTER: Spy pattern - real code executes
async def test_skip_migrations_flag(test_database):
    """Test that skip_migrations flag prevents running migrations."""
    original_run_migrations = Database._run_migrations
    call_count = 0

    def spy_run_migrations(self):
        nonlocal call_count
        call_count += 1
        return original_run_migrations(self)  # Real code executes!

    with patch.object(Database, "_run_migrations", wraps=spy_run_migrations):
        # With skip_migrations=True, migrations should not run
        db1 = Database(test_database.config, skip_migrations=True)
        assert call_count == 0

        # With skip_migrations=False, migrations should run once
        db2 = Database(test_database.config, skip_migrations=False)
        assert call_count == 1

        # Cleanup
        db1.close_sync()
        db2.close_sync()
```

**Estimated Effort**: **45 min - 1 hour** (reduced from 2-4 hours)

**Why reduced:**

- 5 URL tests are straightforward fixture swaps (30 min)
- 1 orchestration test benefits from spy pattern clarity (15 min)
- Spy pattern is simpler than full test rewrites

---

## Available Fixtures for Migration

### Database Fixtures (from `tests/fixtures/database/database_fixtures.py`)

| Fixture              | Type         | Purpose                           |
| -------------------- | ------------ | --------------------------------- |
| `session`            | AsyncSession | Async database session            |
| `session_sync`       | Session      | Sync database session             |
| `test_database`      | TestDatabase | Database instance (async)         |
| `test_database_sync` | TestDatabase | Database instance (sync)          |
| `factory_session`    | Session      | Session configured for FactoryBoy |

### Model Factories (from `tests/fixtures/metadata/metadata_factories.py`)

| Factory                     | ID Base | Purpose             |
| --------------------------- | ------- | ------------------- |
| `AccountFactory`            | 100T    | Account entities    |
| `MediaFactory`              | 200T    | Media files         |
| `PostFactory`               | 300T    | Timeline posts      |
| `GroupFactory`              | 400T    | Conversation groups |
| `MessageFactory`            | 500T    | Direct messages     |
| `AttachmentFactory`         | 600T    | Content attachments |
| `AccountMediaFactory`       | 700T    | Account-Media links |
| `AccountMediaBundleFactory` | 800T    | Media bundles       |

### Pre-built Test Objects (from `tests/fixtures/metadata/metadata_fixtures.py`)

| Fixture           | Provides                             |
| ----------------- | ------------------------------------ |
| `test_account`    | Account with realistic ID            |
| `test_media`      | Video media linked to account        |
| `test_post`       | Post with attachments                |
| `test_message`    | Message with attachments             |
| `test_attachment` | Full chain: Account→Media→Attachment |

---

## Best Practices Already in Use

The metadata tests demonstrate excellent patterns to maintain:

### 1. UUID-Isolated Test Databases

Each test gets a completely isolated PostgreSQL database:

```python
async def test_something(session, test_account):
    # session is connected to unique UUID-named database
    # Perfect isolation - no test pollution
```

### 2. Factory Pattern with Realistic IDs

Factories use 60-bit Snowflake-style IDs:

```python
account = AccountFactory.build(username="test_user")
# ID is realistic: 100000000000001, 100000000000002, etc.
```

### 3. Proper Relationship Testing

Tests verify real SQLAlchemy relationships:

```python
async def test_media_account_relationship(session, test_media):
    await session.refresh(test_media, attribute_names=["account"])
    assert test_media.account is not None
```

### 4. Async Session Handling

Correct async patterns throughout:

```python
async def test_query(session):
    result = await session.execute(select(Account))
    accounts = result.scalars().all()
```

---

## Progress Summary

### Completed: 13 files (93%)

All files except `test_database.py` are fully compliant.

### Remaining: 1 file

| File               | Violations | Est. Effort            | Strategy                    |
| ------------------ | ---------- | ---------------------- | --------------------------- |
| `test_database.py` | ~5         | **45 min - 1 hour** ⚡ | Real fixtures + spy pattern |
| **Total**          | **~5**     | **45 min - 1 hr** ⚡   | Reduced from 2-4 hours      |

---

## Reference

**Fixture Definitions**:

- Database fixtures: `tests/fixtures/database/database_fixtures.py`
- Model factories: `tests/fixtures/metadata/metadata_factories.py`
- Pre-built objects: `tests/fixtures/metadata/metadata_fixtures.py`

**Master conftest**: `tests/conftest.py`

**Last Updated**: 2025-11-21
