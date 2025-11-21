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

| Metric                         | Count         |
| ------------------------------ | ------------- |
| Total test files               | 14            |
| Total test functions           | ~150+         |
| Tests following best practices | ~145          |
| Tests needing migration        | ~5            |
| Estimated migration effort     | **2-4 hours** |

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

---

## File-by-File Migration Guide

### `tests/metadata/test_database.py`

**Current Issues**:

- Some tests may mock database connection methods
- Possible mocking of session creation

**Migration Steps**:

1. Review each test for `MagicMock`, `patch`, or `AsyncMock` usage
2. Replace mocked sessions with `session` or `session_sync` fixtures
3. Use `test_database` fixture for Database instance testing
4. For connection error testing, consider integration tests with invalid credentials

**Example Migration**:

```python
# ❌ BEFORE: Mocked database connection
def test_database_init():
    with patch('metadata.database.create_async_engine') as mock_engine:
        mock_engine.return_value = MagicMock()
        db = Database(connection_string="sqlite:///:memory:")
        mock_engine.assert_called_once()

# ✅ AFTER: Use real database fixture
def test_database_init(test_database):
    # test_database is already a real Database connected to test PostgreSQL
    assert test_database.engine is not None
    assert test_database.async_session_factory is not None
```

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

| File               | Violations | Est. Effort   |
| ------------------ | ---------- | ------------- |
| `test_database.py` | ~5         | 2-4 hours     |
| **Total**          | **~5**     | **2-4 hours** |

---

## Reference

**Fixture Definitions**:

- Database fixtures: `tests/fixtures/database/database_fixtures.py`
- Model factories: `tests/fixtures/metadata/metadata_factories.py`
- Pre-built objects: `tests/fixtures/metadata/metadata_fixtures.py`

**Master conftest**: `tests/conftest.py`

**Last Updated**: 2025-11-18
