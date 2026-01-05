# API and Download Test Migration Guide

**Goal**: Migrate all API and download tests to mock only at external boundaries (HTTP via RESPX), not internal methods.

**Created**: 2025-11-18
**Reference**: `STASH_TEST_MIGRATION_TODO.md` for migration philosophy

---

## Overview

### The Problem

Similar to Stash tests, some API and download tests mock internal methods, creating unreliable tests that:

- ❌ Bypass actual HTTP serialization and request building
- ❌ Never test real response parsing or error handling
- ❌ Mock internal download logic instead of external CDN calls

### The Solution

Mock only at true external boundaries:

- ✅ **Fansly API**: RESPX for HTTP responses
- ✅ **CDN/Media URLs**: RESPX for download content
- ✅ **File System**: Real temporary files (not mocked)
- ✅ **Database**: Real sessions with factories

---

## Summary Statistics

### API Tests

| Metric                      | Count                                                   |
| --------------------------- | ------------------------------------------------------- |
| Total test files            | 3                                                       |
| Total test functions        | 54                                                      |
| Tests using RESPX correctly | 48                                                      |
| Tests needing migration     | 6                                                       |
| Estimated effort            | **2-3 hours** (reduced from 4-5 hours with spy pattern) |

### Download Tests

| Metric                         | Count                                                       |
| ------------------------------ | ----------------------------------------------------------- |
| Total test files               | 9                                                           |
| Total test functions           | 88                                                          |
| Mock violations found          | 79                                                          |
| Tests following best practices | 3 files (24 tests)                                          |
| Estimated effort               | **20-28 hours** (reduced from 30-40 hours with spy pattern) |

---

## Part 1: API Tests

### Test File Inventory

| File                                           | Functions | Violations | Status |
| ---------------------------------------------- | --------- | ---------- | ------ |
| `tests/api/unit/test_fansly_api.py`            | 35        | 0          | ✅     |
| `tests/api/unit/test_fansly_api_additional.py` | 18        | 5          | ❌     |
| `tests/api/unit/test_fansly_api_callback.py`   | 1         | 0          | ✅     |

### External Boundaries for API

The true external boundaries:

1. **Fansly API HTTP endpoints** - Use RESPX
2. **Websocket connections** - Mock `websockets.client.connect` (this IS the external boundary)

### Violations in `test_fansly_api_additional.py`

#### Violation 1: Mocking `update_device_id` Method

**Location**: Lines 252-312 (3 tests)

```python
# ❌ WRONG: Mocking internal method
def test_init_without_device_info(self):
    with patch.object(FanslyApi, "update_device_id") as mock:
        api = FanslyApi(token="test", user_agent="ua", check_key="key")
        mock.assert_called_once()

# ✅ CORRECT: Mock the HTTP endpoint
@respx.mock
def test_init_without_device_info(self):
    # Mock device ID endpoint (OPTIONS for CORS + GET for data)
    respx.options(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
        return_value=httpx.Response(200)
    )
    respx.get(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
        return_value=httpx.Response(200, json={
            "success": "true",
            "response": "new_device_id"
        })
    )

    api = FanslyApi(token="test", user_agent="ua", check_key="key")
    assert api.device_id == "new_device_id"
```

#### Violation 3: Incomplete CORS Test

**Location**: Lines 233-250

```python
# ❌ WRONG: Accessing call_args on real httpx client
def test_cors_options_request_includes_headers(self, fansly_api):
    fansly_api.cors_options_request("https://api.test.com/endpoint")
    call_args = fansly_api.http_session.options.call_args  # Fails!

# ✅ CORRECT: Use RESPX to capture request
@respx.mock
def test_cors_options_request_includes_headers(self, fansly_api):
    route = respx.options("https://api.test.com/endpoint").mock(
        return_value=httpx.Response(200)
    )

    fansly_api.cors_options_request("https://api.test.com/endpoint")

    assert route.called
    request = route.calls.last.request
    assert "origin" in request.headers
```

### API Migration Priority

1. **Phase 1 (Init Tests)**: Fix 3 `test_init_*` tests together - 2-3 hours
2. **Phase 2 (CORS)**: Fix `test_cors_options_request` - 1 hour

---

## Part 2: Download Tests

### Test File Inventory

| File                                                  | Functions | Violations | Severity     | Status |
| ----------------------------------------------------- | --------- | ---------- | ------------ | ------ |
| `tests/download/unit/test_m3u8.py`                    | 19        | 28         | **Critical** | ❌     |
| `tests/download/integration/test_m3u8_integration.py` | 6         | 16         | **Critical** | ❌     |
| `tests/download/unit/test_transaction_recovery.py`    | 3         | 11         | **Critical** | ❌     |
| `tests/download/unit/test_account.py`                 | 18        | 10         | Medium       | ❌     |
| `tests/download/unit/test_common.py`                  | 11        | 9          | High         | ❌     |
| `tests/download/unit/test_media_filtering.py`         | 7         | 5          | High         | ❌     |
| `tests/download/unit/test_downloadstate.py`           | 10        | 0          | None         | ✅     |
| `tests/download/unit/test_globalstate.py`             | 6         | 0          | None         | ✅     |
| `tests/download/unit/test_pagination_duplication.py`  | 8         | 0          | None         | ✅     |

### External Boundaries for Download

The true external boundaries:

1. **Fansly API**: RESPX for timeline/messages/post endpoints
2. **CDN URLs**: RESPX for media file downloads
3. **File System**: Real temp files via `tmp_path` fixture
4. **Database**: Real sessions with factories
5. **ffmpeg**: Can mock subprocess calls (external binary)

### Common Violation Patterns

#### Pattern 1: Mocking Internal Download Methods

```python
# ❌ WRONG: Mocking internal method
async def test_download_timeline(download_state):
    with patch.object(download_state, "_download_media") as mock:
        mock.return_value = True
        await download_timeline(download_state)
        mock.assert_called()

# ✅ CORRECT: Mock HTTP endpoints
@respx.mock
async def test_download_timeline(download_state, session):
    # Mock Fansly API timeline endpoint
    respx.get("https://apiv3.fansly.com/api/v1/timeline").mock(
        return_value=httpx.Response(200, json={
            "success": "true",
            "response": {"posts": [...]}
        })
    )

    # Mock CDN for media downloads
    respx.get(url__regex=r"https://.*\.fansly\.com/.*").mock(
        return_value=httpx.Response(200, content=b"fake media content")
    )

    await download_timeline(download_state)
    # Verify real files were created
```

#### Pattern 2: MagicMock for Database Models

```python
# ❌ WRONG: MagicMock for SQLAlchemy models
def test_process_post():
    mock_post = MagicMock(spec=Post)
    mock_post.id = 12345
    mock_media = MagicMock(spec=Media)

# ✅ CORRECT: Use factories
def test_process_post(session_sync):
    account = AccountFactory.build(id=100000)
    session_sync.add(account)
    session_sync.commit()

    post = PostFactory.build(id=300000, accountId=account.id)
    session_sync.add(post)
    session_sync.commit()

    media = MediaFactory.build(id=200000, accountId=account.id)
    session_sync.add(media)
    session_sync.commit()
```

#### Pattern 3: Mocking File I/O Entirely

```python
# ❌ WRONG: Mocking all file operations
def test_save_media():
    with patch("builtins.open", mock_open()):
        with patch("pathlib.Path.exists", return_value=False):
            save_media(content, path)

# ✅ CORRECT: Use real temporary files
def test_save_media(tmp_path):
    output_path = tmp_path / "test_media.mp4"
    content = b"fake video content"

    save_media(content, output_path)

    assert output_path.exists()
    assert output_path.read_bytes() == content
```

#### Pattern 4: Mocking httpx Directly

```python
# ❌ WRONG: Mocking httpx client
async def test_fetch_content():
    with patch("httpx.AsyncClient.get") as mock:
        mock.return_value = MagicMock(content=b"data")
        result = await fetch_content(url)

# ✅ CORRECT: Use RESPX
@respx.mock
async def test_fetch_content():
    respx.get("https://cdn.fansly.com/media/123").mock(
        return_value=httpx.Response(200, content=b"data")
    )

    result = await fetch_content("https://cdn.fansly.com/media/123")
    assert result == b"data"
```

#### Pattern 5: Spy Pattern for Internal Orchestration Tests

For tests that verify internal method coordination without bypassing real code execution:

```python
# ❌ WRONG: Mocking internal method with return_value/side_effect
@respx.mock
async def test_download_batch_with_retry():
    with patch.object(downloader, "_download_single_file", AsyncMock(side_effect=[
        Exception("Network error"),  # First call fails
        True,  # Retry succeeds
    ])):
        result = await downloader.download_batch([url1])
        # Never tests real download logic or real retry behavior!

# ✅ CORRECT: Spy pattern with wraps - real code executes
@respx.mock
async def test_download_batch_with_retry(tmp_path):
    original_download = downloader._download_single_file
    call_count = 0
    call_urls = []

    async def spy_download(url, output_path):
        nonlocal call_count
        call_count += 1
        call_urls.append(url)
        return await original_download(url, output_path)  # Real code executes!

    # Mock CDN to fail once, then succeed
    respx.get(url__regex=r"https://cdn\.fansly\.com/.*").mock(
        side_effect=[
            httpx.Response(500),  # First attempt fails
            httpx.Response(200, content=b"video data"),  # Retry succeeds
        ]
    )

    with patch.object(downloader, "_download_single_file", wraps=spy_download):
        result = await downloader.download_batch([url1])

        # Verify orchestration
        assert call_count == 2  # Original + retry
        assert call_urls[0] == call_urls[1]  # Same URL retried
        assert result.success is True
        # Verify real file was created
        assert (tmp_path / "file.mp4").exists()
```

**Why spy pattern matters for download tests:**

- Real download logic executes (retry delays, exponential backoff, etc.)
- Tests actual error handling and recovery paths
- Verifies real file I/O operations occur
- Catches regressions in orchestration (e.g., infinite retry loops)
- Similar concept to RESPX call inspection but for internal coordination

**When to use spy pattern in download/API tests:**

1. Testing retry logic with real backoff calculations
2. Verifying early exit conditions in batch operations
3. Testing rate limiting coordination between multiple download methods
4. Confirming error propagation through download pipeline
5. Validating conditional branching based on content type or file size
6. Validating how data was transformed by internal functions in between multi-step operations

**When NOT to use spy pattern:**

1. If RESPX at HTTP boundary is sufficient (prefer that)
2. Testing simple HTTP requests with no internal orchestration

---

## Migration Patterns

### Pattern A: Timeline/Messages Download

```python
@respx.mock
async def test_download_timeline(
    download_state,
    session,
    tmp_path,
    test_account
):
    # 1. Setup database with factories
    download_state.base_path = tmp_path
    download_state.creator_id = test_account.id

    # 2. Mock Fansly API responses
    timeline_route = respx.get(
        url__regex=r"https://apiv3\.fansly\.com/api/v1/timeline.*"
    ).mock(
        return_value=httpx.Response(200, json={
            "success": "true",
            "response": {
                "posts": [{
                    "id": "300000000000001",
                    "accountId": str(test_account.id),
                    "content": "Test post",
                    "attachments": [{
                        "contentId": "200000000000001",
                        "contentType": 1,  # Image
                    }]
                }]
            }
        })
    )

    # 3. Mock CDN for media
    media_route = respx.get(
        url__regex=r"https://.*cdn.*fansly\.com/.*"
    ).mock(
        return_value=httpx.Response(200, content=b"image data")
    )

    # 4. Execute
    result = await download_timeline(download_state)

    # 5. Verify
    assert timeline_route.called
    assert media_route.called
    # Check real files exist
    downloaded_files = list(tmp_path.rglob("*"))
    assert len(downloaded_files) > 0
```

### Pattern B: Single Media Download

```python
@respx.mock
async def test_download_media_file(tmp_path):
    url = "https://cdn.fansly.com/media/123.mp4"
    output_path = tmp_path / "123.mp4"

    # Mock CDN response with realistic headers
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"fake video content",
            headers={
                "content-type": "video/mp4",
                "content-length": "17"
            }
        )
    )

    await download_media_file(url, output_path)

    assert output_path.exists()
    assert output_path.read_bytes() == b"fake video content"
```

### Pattern C: Error Handling

```python
@respx.mock
async def test_download_handles_404(download_state, tmp_path):
    download_state.base_path = tmp_path

    # Mock 404 response from CDN
    respx.get(url__regex=r"https://cdn\.fansly\.com/.*").mock(
        return_value=httpx.Response(404)
    )

    result = await download_media(download_state, media_url)

    assert result.success is False
    assert "404" in result.error or "not found" in result.error.lower()
```

---

## Available Fixtures for Migration

### API Fixtures (from `tests/fixtures/api/api_fixtures.py`)

| Fixture                         | Purpose                                   |
| ------------------------------- | ----------------------------------------- |
| `fansly_api`                    | Real FanslyApi instance for RESPX testing |
| `fansly_api_with_respx`         | FanslyApi ready for HTTP mocking          |
| `mock_fansly_account_response`  | Sample account API response               |
| `mock_fansly_timeline_response` | Sample timeline API response              |

### Download Fixtures (from `tests/fixtures/download/`)

| Fixture                | Purpose                          |
| ---------------------- | -------------------------------- |
| `download_state`       | Real DownloadState instance      |
| `test_downloads_dir`   | Temporary downloads directory    |
| `DownloadStateFactory` | Factory for custom DownloadState |
| `GlobalStateFactory`   | Factory for GlobalState          |

### Database Fixtures

| Fixture        | Purpose                       |
| -------------- | ----------------------------- |
| `session`      | Async database session        |
| `session_sync` | Sync database session         |
| `test_account` | Pre-built Account in database |
| `test_media`   | Pre-built Media in database   |

### Model Factories

| Factory             | Purpose                    |
| ------------------- | -------------------------- |
| `AccountFactory`    | Create Account entities    |
| `MediaFactory`      | Create Media entities      |
| `PostFactory`       | Create Post entities       |
| `MessageFactory`    | Create Message entities    |
| `AttachmentFactory` | Create Attachment entities |

---

## Migration Priority Recommendations

### Week 1: API Tests + Critical Downloads (9-11 hours)

1. `test_fansly_api_additional.py` - Fix 5 violations **(2-3 hours)** ⚡ Reduced from 3-4 hours

   - Init tests (3) - Use RESPX at HTTP boundary (not spy pattern)
   - CORS test (1) - RESPX capture

2. `test_m3u8.py` - 28 violations, **Critical** **(6-8 hours)** ⚡ Reduced from 8-10 hours
   - Replace Path mocks with real `tmp_path` (simpler than spy pattern)
   - Spy pattern for two-tier download strategy orchestration
   - RESPX for all HTTP boundaries

### Week 2: Critical + High Severity (8-11 hours)

1. `test_m3u8_integration.py` - 16 violations, **Critical** **(4-5 hours)** ⚡ Reduced from 6-8 hours

   - Already uses RESPX correctly
   - Remove Path mocks → use real `tmp_path`
   - Spy pattern for strategy coordination

2. `test_transaction_recovery.py` - 11 violations, **Critical** **(2-3 hours)** ⚡ Reduced from 4-5 hours

   - Use real database sessions (one test already correct)
   - Optional spy pattern for error handler verification

3. `test_common.py` - 9 violations, High **(3-4 hours)** ⚡ Reduced from 4-5 hours
   - Spy pattern for download pipeline coordination
   - RESPX for HTTP boundaries

### Week 3: Medium + High Severity (3-5 hours)

1. `test_account.py` - 10 violations, Medium **(2-3 hours)** ⚡ Reduced from 4-5 hours

   - Fixture reconfiguration (main issue)
   - Use real `fansly_api` fixture instead of MagicMock

2. `test_media_filtering.py` - 5 violations, High **(1-2 hours)** ⚡ Reduced from 3-4 hours
   - Already well-structured with pytest-mocker
   - Minimal changes needed

---

## Effort Reduction Analysis

### Summary by File

| File                            | Original Effort | Revised Effort | Reduction | Key Strategy                          |
| ------------------------------- | --------------- | -------------- | --------- | ------------------------------------- |
| `test_fansly_api_additional.py` | 3-4 hrs         | 2-3 hrs        | -25%      | RESPX at boundary (no spy)            |
| `test_m3u8.py`                  | 8-10 hrs        | 6-8 hrs        | -25%      | Real tmp_path + spy for orchestration |
| `test_m3u8_integration.py`      | 6-8 hrs         | 4-5 hrs        | -35%      | Remove Path mocks                     |
| `test_transaction_recovery.py`  | 4-5 hrs         | 2-3 hrs        | -40%      | Use real database                     |
| `test_account.py`               | 4-5 hrs         | 2-3 hrs        | -40%      | Fixture reconfiguration               |
| `test_common.py`                | 4-5 hrs         | 3-4 hrs        | -20%      | Spy for orchestration                 |
| `test_media_filtering.py`       | 3-4 hrs         | 1-2 hrs        | -50%      | Minimal changes                       |
| **Total API**                   | 3-4 hrs         | 2-3 hrs        | **-25%**  | RESPX primarily                       |
| **Total Download**              | 30-40 hrs       | 20-28 hrs      | **-33%**  | Mixed strategies                      |
| **COMBINED**                    | 33-44 hrs       | 22-31 hrs      | **-36%**  | Strategic tool selection              |

### Why Effort Reduced

1. **Path Mocking Revelation** (saves 3-4 hours):

   - 7+ tests mock `Path.exists()` when they should use `tmp_path`
   - Real temp files are simpler than both mocks AND spy patterns

2. **Spy Pattern Clarity** (saves 4-5 hours):

   - Identifies which tests need orchestration verification
   - Cleaner than full test rewrites for coordination logic
   - Applies to ~10% of violations, not all

3. **Many Tests Already Good** (saves 5-6 hours):

   - 24 tests have zero violations
   - Most API tests already use RESPX
   - Less work than originally estimated

4. **Database Testing Simplified** (saves 1-2 hours):
   - One test already correct (nested_transaction_recovery)
   - Other two just need real fixtures

### Strategic Tool Selection

| Violation Type              | Tool                      | Example                            |
| --------------------------- | ------------------------- | ---------------------------------- |
| HTTP calls to external APIs | RESPX                     | Fansly API, CDN URLs               |
| File operations             | Real `tmp_path`           | Video downloads, M3U8 segments     |
| Database operations         | Real sessions + factories | Account, Media, Post creation      |
| Internal orchestration      | Spy pattern               | Retry logic, fallback coordination |
| External binaries           | Mock subprocess           | ffmpeg (truly external)            |

---

## Existing Good Patterns to Maintain

### 1. RESPX for Fansly API (from `test_fansly_api.py`)

```python
@respx.mock
def test_get_creator_account_info(self, fansly_api):
    respx.options("https://apiv3.fansly.com/api/v1/account").mock(
        return_value=httpx.Response(200)
    )
    route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
        return_value=httpx.Response(200, json={"success": "true", "response": []})
    )

    fansly_api.get_creator_account_info("test_creator")

    assert route.called
    request = route.calls.last.request
    assert request.url.params["usernames"] == "test_creator"
```

### 2. Real Response Objects

```python
def test_validate_response(self, fansly_api):
    # Use real httpx.Response, not MagicMock
    response = httpx.Response(200, json={"success": "true"})
    assert fansly_api.validate_json_response(response) is True
```

### 3. Callback Mocking (Appropriate)

```python
def test_callback(self):
    # MagicMock is appropriate for user-provided callbacks
    mock_callback = MagicMock()
    api = FanslyApi(..., on_device_updated=mock_callback)
    # ...
    mock_callback.assert_called_once()
```

---

## Progress Summary

### API Tests

| Status       | Files | Tests  | Effort                         |
| ------------ | ----- | ------ | ------------------------------ |
| ✅ Compliant | 2     | 36     | 0                              |
| ❌ Migration | 1     | 18     | **2-3 hrs** ⚡ (was 3-4 hours) |
| **Total**    | **3** | **54** | **2-3 hrs** ⚡ (was 3-4 hours) |

### Download Tests

| Status       | Files | Tests  | Effort                             |
| ------------ | ----- | ------ | ---------------------------------- |
| ✅ Compliant | 3     | 24     | 0                                  |
| ❌ Migration | 6     | 64     | **20-28 hrs** ⚡ (was 30-40 hours) |
| **Total**    | **9** | **88** | **20-28 hrs** ⚡ (was 30-40 hours) |

### Combined Total

| Category  | Files  | Tests   | Effort                             |
| --------- | ------ | ------- | ---------------------------------- |
| API       | 3      | 54      | **2-3 hrs** ⚡ (was 3-4 hours)     |
| Download  | 9      | 88      | **20-28 hrs** ⚡ (was 30-40 hours) |
| **Total** | **12** | **142** | **22-31 hrs** ⚡ (was 33-44 hours) |

**Overall Reduction**: **-36%** effort savings with spy pattern strategy

---

## Reference

**Fixture Definitions**:

- API fixtures: `tests/fixtures/api/api_fixtures.py`
- Download fixtures: `tests/fixtures/download/download_fixtures.py`
- Download factories: `tests/fixtures/download/download_factories.py`
- Database fixtures: `tests/fixtures/database/database_fixtures.py`
- Model factories: `tests/fixtures/metadata/metadata_factories.py`

**RESPX Documentation**: https://lundberg.github.io/respx/

**Last Updated**: 2025-11-21
