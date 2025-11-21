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

| Metric                      | Count         |
| --------------------------- | ------------- |
| Total test files            | 3             |
| Total test functions        | 54            |
| Tests using RESPX correctly | 48            |
| Tests needing migration     | 6             |
| Estimated effort            | **4-5 hours** |

### Download Tests

| Metric                         | Count              |
| ------------------------------ | ------------------ |
| Total test files               | 9                  |
| Total test functions           | 88                 |
| Mock violations found          | 79                 |
| Tests following best practices | 3 files (24 tests) |
| Estimated effort               | **30-40 hours**    |

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

### Week 1: API Tests + Critical Downloads (12-15 hours)

1. `test_fansly_api_additional.py` - Fix 4 violations (3-4 hours)

   - Init tests (3) - Share same pattern
   - CORS test (1) - RESPX capture

2. `test_m3u8.py` - 28 violations, **Critical** (8-10 hours)
   - Extensive internal function patches
   - Path/file mocking that should use real tmp_path
   - Complex mock chaining for HTTP responses

### Week 2: Critical + High Severity (15-20 hours)

1. `test_m3u8_integration.py` - 16 violations, **Critical** (6-8 hours)

   - Similar patterns to test_m3u8.py
   - Already uses RESPX in some places (build on this)

2. `test_transaction_recovery.py` - 11 violations, **Critical** (4-5 hours)

   - Mocked database sessions → use real test_database fixture
   - Internal error handler patches

3. `test_common.py` - 9 violations, High (4-5 hours)
   - Internal function patches (process_media_info, download_media)

### Week 3: Medium + High Severity (12-15 hours)

1. `test_account.py` - 10 violations, Medium (4-5 hours)

   - Internal API wrapper mocks
   - Config fixture improvements

2. `test_media_filtering.py` - 5 violations, High (3-4 hours)
   - pytest-mocker patches on media processing
   - Good structure, just needs mock elimination

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

| Status       | Files | Tests  | Effort        |
| ------------ | ----- | ------ | ------------- |
| ✅ Compliant | 2     | 36     | 0             |
| ❌ Migration | 1     | 18     | 3-4 hours     |
| **Total**    | **3** | **54** | **3-4 hours** |

### Download Tests

| Status       | Files | Tests  | Effort          |
| ------------ | ----- | ------ | --------------- |
| ✅ Compliant | 3     | 24     | 0               |
| ❌ Migration | 6     | 64     | 30-40 hours     |
| **Total**    | **9** | **88** | **30-40 hours** |

### Combined Total

| Category  | Files  | Tests   | Effort          |
| --------- | ------ | ------- | --------------- |
| API       | 3      | 54      | 3-4 hours       |
| Download  | 9      | 88      | 30-40 hours     |
| **Total** | **12** | **142** | **33-44 hours** |

---

## Reference

**Fixture Definitions**:

- API fixtures: `tests/fixtures/api/api_fixtures.py`
- Download fixtures: `tests/fixtures/download/download_fixtures.py`
- Download factories: `tests/fixtures/download/download_factories.py`
- Database fixtures: `tests/fixtures/database/database_fixtures.py`
- Model factories: `tests/fixtures/metadata/metadata_factories.py`

**RESPX Documentation**: https://lundberg.github.io/respx/

**Last Updated**: 2025-11-18
