# Fansly Downloader - API Testing Requirements

**Goal**: Ensure comprehensive test coverage with mocking only at external boundaries (HTTP via RESPX).

**Created**: 2026-01-09
**Project**: fansly-downloader-ng
**Status**: Test Migration in Progress

---

## Overview

### Testing Philosophy

This project follows strict testing principles adapted from production-tested GraphQL client patterns:

- ✅ **Mock at HTTP boundary only** - Use `respx` to mock Fansly API HTTP responses
- ✅ **Test real code paths** - Execute actual API client methods, request building, response parsing
- ✅ **Verify request AND response** - Every HTTP call must assert both request structure AND response data
- ✅ **Use real models** - Test actual SQLAlchemy model construction and Pydantic validation
- ❌ **Never mock internal methods** - No mocking of download logic, helper functions, or internal orchestration

### Key Differences from GraphQL Testing

While this project adapts patterns from Stash GraphQL client testing, there are important differences:

| Aspect             | GraphQL (Stash)                    | REST API (Fansly)                        |
| ------------------ | ---------------------------------- | ---------------------------------------- |
| Protocol           | GraphQL queries/mutations          | REST endpoints                           |
| Request format     | JSON with `query` and `variables`  | URL params, JSON body                    |
| Response structure | `{"data": {...}, "errors": [...]}` | `{"success": "true", "response": {...}}` |
| HTTP methods       | Primarily POST                     | GET, POST, OPTIONS (CORS)                |
| Authentication     | Headers/cookies                    | Bearer token + device ID                 |
| Client library     | GQL client (gql)                   | httpx AsyncClient                        |

---

## External Boundaries

The true external boundaries for this project:

1. **Fansly API HTTP endpoints** (`https://apiv3.fansly.com/api/v1/*`)

   - Mock using RESPX
   - Includes CORS OPTIONS requests
   - GET and POST methods

2. **CDN URLs** (`https://*.fansly.com/*`)

   - Mock media file downloads
   - Mock M3U8 playlist downloads
   - Mock segment downloads

3. **File System**

   - Use real temporary files via `tmp_path` fixture
   - Never mock `Path.exists()`, `Path.read_bytes()`, etc.

4. **Database**

   - Use real async sessions with factories
   - Never use MagicMock for SQLAlchemy models

5. **External Binaries** (Optional)
   - Can mock subprocess calls to `ffmpeg` (truly external)

### What NOT to Mock

- ❌ Internal FanslyApi methods (e.g., `update_device_id`, `_build_request`)
- ❌ Download orchestration logic (e.g., `_download_media`, `_process_post`)
- ❌ Database operations (use real sessions)
- ❌ File I/O (use real temp files)
- ❌ Request builders or response parsers

---

## Core Testing Patterns

### Pattern 1: HTTP-Only Mocking with RESPX

All API tests mock Fansly API responses at the HTTP boundary using `respx`:

```python
import respx
import httpx
import pytest
from api import FanslyApi

@pytest.mark.asyncio
@respx.mock
async def test_get_creator_account_info(fansly_api):
    """Test fetching creator account information."""

    # Mock CORS preflight (Fansly API requires this)
    respx.options("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[httpx.Response(200)]
    )

    # Mock the actual API response
    account_route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": "true",
                    "response": [{
                        "id": "100000000000001",
                        "username": "test_creator",
                        "displayName": "Test Creator",
                        "profileAccessFlags": 1,
                    }]
                }
            )
        ]
    )

    # Execute real client code
    try:
        result = await fansly_api.get_creator_account_info("test_creator")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(account_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify the result
    assert result is not None
    assert result[0]["username"] == "test_creator"

    # REQUIRED: Verify HTTP request
    assert account_route.called
    request = account_route.calls.last.request
    assert "usernames" in request.url.params
    assert request.url.params["usernames"] == "test_creator"
    assert "authorization" in request.headers
```

### Pattern 2: Multiple HTTP Calls

When API methods make sequential HTTP calls, provide responses for ALL calls:

```python
@pytest.mark.asyncio
@respx.mock
async def test_api_with_pagination(fansly_api):
    """Test API method that makes multiple paginated requests."""

    # Mock both CORS and actual requests for each page
    respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
        side_effect=[
            httpx.Response(200),
            httpx.Response(200),
        ]
    )

    timeline_route = respx.get(
        url__regex=r"https://apiv3\.fansly\.com/api/v1/timeline.*"
    ).mock(
        side_effect=[
            # Page 1
            httpx.Response(200, json={
                "success": "true",
                "response": {
                    "posts": [{"id": "1", "content": "Post 1"}],
                    "aggregationData": {"hasMore": True}
                }
            }),
            # Page 2 (last page)
            httpx.Response(200, json={
                "success": "true",
                "response": {
                    "posts": [{"id": "2", "content": "Post 2"}],
                    "aggregationData": {"hasMore": False}
                }
            }),
        ]
    )

    # Execute real pagination logic
    try:
        all_posts = await fansly_api.get_all_timeline_posts("100000000000001")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(timeline_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify both calls were made
    assert len(timeline_route.calls) == 2

    # Verify first request
    req0 = timeline_route.calls[0].request
    assert "before" not in req0.url.params  # First page has no cursor

    # Verify second request (pagination)
    req1 = timeline_route.calls[1].request
    assert "before" in req1.url.params  # Pagination cursor present

    # Verify results
    assert len(all_posts) == 2
```

### Pattern 3: Testing CORS Preflight

Fansly API requires CORS preflight for many endpoints:

```python
@pytest.mark.asyncio
@respx.mock
async def test_cors_options_request_includes_headers(fansly_api):
    """Test that CORS OPTIONS requests include required headers."""

    options_route = respx.options("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[httpx.Response(200)]
    )

    # Execute CORS preflight
    try:
        fansly_api.cors_options_request("https://apiv3.fansly.com/api/v1/account")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(options_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify request headers
    assert options_route.called
    request = options_route.calls.last.request
    assert "origin" in request.headers
    assert request.headers["origin"] == "https://fansly.com"
    assert "access-control-request-method" in request.headers
```

### Pattern 4: Testing Error Handling

Test real API errors from Fansly:

```python
@pytest.mark.asyncio
@respx.mock
async def test_api_handles_unauthorized(fansly_api):
    """Test handling of 401 Unauthorized response."""

    error_route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[
            httpx.Response(
                401,
                json={"success": "false", "error": "Invalid authorization token"}
            )
        ]
    )

    try:
        with pytest.raises(Exception, match="Invalid authorization token"):
            await fansly_api.get_creator_account_info("test_creator")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(error_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

@pytest.mark.asyncio
@respx.mock
async def test_api_handles_rate_limiting(fansly_api):
    """Test handling of 429 Rate Limit response."""

    rate_limit_route = respx.get("https://apiv3.fansly.com/api/v1/timeline").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "60"}
            )
        ]
    )

    try:
        with pytest.raises(Exception, match="Rate limit|429"):
            await fansly_api.get_timeline_posts("100000000000001")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(rate_limit_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")
```

### Pattern 5: Testing Media Downloads

Mock CDN responses for file downloads:

```python
@pytest.mark.asyncio
@respx.mock
async def test_download_media_file(tmp_path):
    """Test downloading a single media file."""

    url = "https://cdn.fansly.com/media/123456789.mp4"
    output_path = tmp_path / "123456789.mp4"

    # Mock CDN response with realistic headers
    media_route = respx.get(url).mock(
        side_effect=[
            httpx.Response(
                200,
                content=b"fake video content",
                headers={
                    "content-type": "video/mp4",
                    "content-length": "18"
                }
            )
        ]
    )

    # Execute real download code
    try:
        await download_media_file(url, output_path)
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(media_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify HTTP request
    assert media_route.called

    # Verify real file was created
    assert output_path.exists()
    assert output_path.read_bytes() == b"fake video content"
    assert output_path.stat().st_size == 18
```

---

## Critical Requirements

### Requirement 1: HTTP Call Assertions (MANDATORY)

**Every test with HTTP calls MUST verify:**

1. **Call count** - Exact number of HTTP requests made
2. **Request content** - URL, params, headers, and body for each call
3. **Response handling** - Verify data was parsed and used correctly

```python
# ❌ WRONG: No assertion on HTTP calls
@respx.mock
async def test_bad_example(fansly_api):
    respx.get("https://apiv3.fansly.com/api/v1/account").mock(...)
    result = await fansly_api.get_creator_account_info("test")
    assert result[0]["username"] == "test"  # Missing call verification!

# ✅ CORRECT: Complete verification
@respx.mock
async def test_good_example(fansly_api):
    account_route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(...)

    result = await fansly_api.get_creator_account_info("test")

    # REQUIRED: Verify exact call count
    assert account_route.called
    assert len(account_route.calls) == 1

    # REQUIRED: Verify request
    request = account_route.calls.last.request
    assert request.url.params["usernames"] == "test"
    assert "authorization" in request.headers

    # REQUIRED: Verify response was used correctly
    assert result[0]["username"] == "test"
```

### Requirement 2: Complete Response Schemas

All Fansly API mock responses must match actual API structure:

```python
# ❌ WRONG: Incomplete response
{
    "success": "true",
    "response": [{
        "id": "123",
        "username": "test"
        # Missing required fields!
    }]
}

# ✅ CORRECT: Complete account schema
{
    "success": "true",
    "response": [{
        "id": "100000000000001",
        "username": "test_creator",
        "displayName": "Test Creator",
        "profileAccessFlags": 1,
        "timelineStats": {
            "postCount": 10,
            "imageCount": 5,
            "videoCount": 5
        },
        "location": None,
        "about": None,
        # ... all required fields
    }]
}
```

### Requirement 3: No Internal Method Mocking

Never mock internal API or download methods:

```python
# ❌ WRONG: Mocking internal method
from unittest.mock import patch, AsyncMock

with patch.object(fansly_api, 'update_device_id', AsyncMock()):
    result = await fansly_api.get_creator_account_info("test")

# ✅ CORRECT: Mock only HTTP
@respx.mock
def test_example(fansly_api):
    api_route = respx.get(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
        side_effect=[
            httpx.Response(200, json={"success": "true", "response": {...}})
        ]
    )
    try:
        result = await fansly_api.get_creator_account_info("test")
    finally:
        print("****RESPX Call Debugging****")
        for index, call in enumerate(api_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")
```

### Requirement 4: Real File System Operations

Test real file I/O with temporary files:

```python
# ❌ WRONG: Mocking file operations
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

---

## Advanced Testing Patterns

### Spy Pattern for Internal Orchestration Tests

For tests that need to verify internal method coordination while still executing real code:

```python
# ❌ WRONG: Mocking internal method with return_value
@respx.mock
async def test_download_with_retry_wrong():
    with patch.object(downloader, "_download_single_file", AsyncMock(side_effect=[
        Exception("Network error"),  # First call fails
        True,  # Retry succeeds
    ])):
        result = await downloader.download_batch([url])
        # Never tests real download logic or real retry behavior!

# ✅ CORRECT: Spy pattern - real code executes
@respx.mock
async def test_download_with_retry_correct(tmp_path):
    original_download = downloader._download_single_file
    call_count = 0
    call_args = []

    async def spy_download(url, output_path):
        nonlocal call_count
        call_count += 1
        call_args.append((url, output_path))
        return await original_download(url, output_path)  # Real code executes!

    # Mock CDN to fail once, then succeed
    respx.get(url__regex=r"https://cdn\.fansly\.com/.*").mock(
        side_effect=[
            httpx.Response(500),  # First attempt fails
            httpx.Response(200, content=b"video data"),  # Retry succeeds
        ]
    )

    with patch.object(downloader, "_download_single_file", wraps=spy_download):
        result = await downloader.download_batch([url])

        # Verify orchestration
        assert call_count == 2  # Original + retry
        assert call_args[0][0] == call_args[1][0]  # Same URL
        assert result.success is True

        # Verify real file was created
        assert (tmp_path / "file.mp4").exists()
```

**Why spy pattern matters:**

- ✅ Real code path executes (not mocked behavior)
- ✅ Tests actual retry logic with delays and backoff
- ✅ Catches regressions in orchestration
- ✅ Verifies real file I/O operations occur

**When to use spy pattern:**

- Testing retry logic with exponential backoff
- Verifying early return conditions in batch operations
- Testing rate limiting coordination
- Confirming error propagation through download pipeline
- Validating how data was transformed between multi-step operations

**When NOT to use spy pattern:**

- If RESPX at HTTP boundary is sufficient (prefer that)
- Testing simple HTTP requests with no internal orchestration

---

## Valid Exceptions to Mock-Free Testing

While the general rule is "mock only at HTTP boundaries," there are legitimate exceptions:

### Exception 1: Edge Case Coverage

Testing error handling paths that are difficult to trigger via HTTP:

```python
# ✅ ACCEPTABLE: Simulating rare failure condition
original_parse = fansly_api._parse_response

def failing_parse(response):
    raise Exception("Unexpected JSON decode failure")

with patch.object(fansly_api, "_parse_response", side_effect=failing_parse):
    with pytest.raises(Exception, match="Unexpected JSON decode failure"):
        await fansly_api.get_creator_account_info("test")
```

**Justification**: Some error conditions (memory errors, parse exceptions) are difficult to simulate via HTTP.

### Exception 2: External Binaries

Mocking truly external processes:

```python
# ✅ ACCEPTABLE: Mocking ffmpeg subprocess
with patch("subprocess.run") as mock_ffmpeg:
    mock_ffmpeg.return_value = MagicMock(returncode=0)
    result = await convert_video_format(input_path, output_path)
    mock_ffmpeg.assert_called_once()
```

**Justification**: Testing without requiring ffmpeg installation in CI/CD.

### Exception 3: User Callbacks

Mocking user-provided callback functions:

```python
# ✅ ACCEPTABLE: Testing callback invocation
mock_callback = MagicMock()
api = FanslyApi(..., on_device_updated=mock_callback)
# ... perform action
mock_callback.assert_called_once_with("new_device_id")
```

**Justification**: Callbacks are external to the library and user-controlled.

### Review Process for Exceptions

**Before mocking internal methods, ask:**

1. ☑ Can this be tested with RESPX at the HTTP boundary?
2. ☑ Does this test actually need to verify orchestration (spy pattern)?
3. ☑ Is the complexity of HTTP mocking truly prohibitive?
4. ☑ Have I documented WHY mocking is necessary?

---

## Common Test Patterns

### Testing Timeline/Posts Download

```python
@pytest.mark.asyncio
@respx.mock
async def test_download_timeline_posts(download_state, session, tmp_path, test_account):
    """Test downloading creator timeline posts with media."""

    # Setup
    download_state.base_path = tmp_path
    download_state.creator_id = test_account.id

    # Mock Fansly API
    respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
        side_effect=[httpx.Response(200)]
    )

    timeline_route = respx.get(
        url__regex=r"https://apiv3\.fansly\.com/api/v1/timeline.*"
    ).mock(
        side_effect=[
            httpx.Response(200, json={
                "success": "true",
                "response": {
                    "posts": [{
                        "id": "300000000000001",
                        "accountId": str(test_account.id),
                        "content": "Test post",
                        "attachments": [{
                            "contentId": "200000000000001",
                            "contentType": 1,  # Image
                            "locations": [{
                                "location": "https://cdn.fansly.com/image/123.jpg"
                            }]
                        }]
                    }],
                    "aggregationData": {"hasMore": False}
                }
            })
        ]
    )

    # Mock CDN
    media_route = respx.get("https://cdn.fansly.com/image/123.jpg").mock(
        side_effect=[httpx.Response(200, content=b"image data")]
    )

    # Execute
    try:
        result = await download_timeline(download_state)
    finally:
        print("****RESPX Call Debugging****")
        print("Timeline route calls:")
        for index, call in enumerate(timeline_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")
        print("Media route calls:")
        for index, call in enumerate(media_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify HTTP calls
    assert timeline_route.called
    assert media_route.called

    # Verify real files
    downloaded_files = list(tmp_path.rglob("*.jpg"))
    assert len(downloaded_files) == 1
    assert downloaded_files[0].read_bytes() == b"image data"
```

### Testing M3U8 Playlist Downloads

```python
@pytest.mark.asyncio
@respx.mock
async def test_download_m3u8_video(tmp_path):
    """Test downloading M3U8 video with segments."""

    playlist_url = "https://cdn.fansly.com/video/123/master.m3u8"
    output_path = tmp_path / "video.mp4"

    # Mock M3U8 playlist
    playlist_route = respx.get(playlist_url).mock(
        side_effect=[
            httpx.Response(
                200,
                content=b"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment0.ts
#EXTINF:10.0,
segment1.ts
#EXT-X-ENDLIST
""",
                headers={"content-type": "application/vnd.apple.mpegurl"}
            )
        ]
    )

    # Mock segments
    base_url = "https://cdn.fansly.com/video/123/"
    segment0_route = respx.get(f"{base_url}segment0.ts").mock(
        side_effect=[httpx.Response(200, content=b"segment 0 data")]
    )
    segment1_route = respx.get(f"{base_url}segment1.ts").mock(
        side_effect=[httpx.Response(200, content=b"segment 1 data")]
    )

    # Execute
    try:
        await download_m3u8_video(playlist_url, output_path)
    finally:
        print("****RESPX Call Debugging****")
        print("Playlist route calls:")
        for index, call in enumerate(playlist_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")
        print("Segment 0 route calls:")
        for index, call in enumerate(segment0_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")
        print("Segment 1 route calls:")
        for index, call in enumerate(segment1_route.calls):
            print(f"Call {index}")
            print(f"--request: {call.request}")
            print(f"--response: {call.response}")

    # Verify segments were downloaded
    segment_dir = tmp_path / "segments"
    assert (segment_dir / "segment0.ts").exists()
    assert (segment_dir / "segment1.ts").exists()

    # Verify concatenated output (if applicable)
    if output_path.exists():
        assert output_path.stat().st_size > 0
```

### Testing Database Integration

```python
@pytest.mark.asyncio
async def test_save_post_to_database(session, test_account):
    """Test saving post metadata to database."""

    post_data = {
        "id": "300000000000001",
        "accountId": test_account.id,
        "content": "Test post content",
        "createdAt": 1234567890,
    }

    # Use real factory
    post = PostFactory.build(**post_data)
    session.add(post)
    await session.commit()

    # Verify in database
    result = await session.execute(
        select(Post).where(Post.id == "300000000000001")
    )
    saved_post = result.scalar_one()

    assert saved_post.content == "Test post content"
    assert saved_post.accountId == test_account.id
```

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Incomplete HTTP Verification

```python
# WRONG: Only verifies result, not the HTTP call
@respx.mock
async def test_bad(fansly_api):
    respx.get(...).mock(...)
    result = await fansly_api.get_creator_account_info("test")
    assert result[0]["username"] == "test"  # Missing verification!
```

### ❌ Anti-Pattern 2: Mocking Internal Methods

```python
# WRONG: Mocking internal API method
with patch.object(fansly_api, 'get_creator_account_info', AsyncMock(...)):
    result = await download_creator_content(creator_id)
```

### ❌ Anti-Pattern 3: Mocking File Operations

```python
# WRONG: Mocking Path operations
with patch("pathlib.Path.exists", return_value=True):
    with patch("pathlib.Path.read_bytes", return_value=b"data"):
        result = load_media_file(path)
```

### ❌ Anti-Pattern 4: MagicMock for Models

```python
# WRONG: MagicMock for SQLAlchemy models
mock_account = MagicMock(spec=Account)
mock_account.id = 100000000000001
```

### ❌ Anti-Pattern 5: Not Testing Error Cases

```python
# INCOMPLETE: Only tests success case
@respx.mock
async def test_get_timeline(fansly_api):
    timeline_route = respx.get(...).mock(side_effect=[success_response])
    result = await fansly_api.get_timeline_posts(account_id)
    assert len(result) > 0

# ALSO TEST: 401, 404, 429, network errors, malformed JSON
```

---

## Available Fixtures

### API Fixtures

Located in `tests/fixtures/api/api_fixtures.py`:

| Fixture                         | Purpose                                         |
| ------------------------------- | ----------------------------------------------- |
| `fansly_api`                    | Real FanslyApi instance ready for RESPX testing |
| `fansly_api_with_respx`         | FanslyApi pre-configured for HTTP mocking       |
| `mock_fansly_account_response`  | Sample account API response                     |
| `mock_fansly_timeline_response` | Sample timeline API response                    |

### Download Fixtures

Located in `tests/fixtures/download/`:

| Fixture                | Purpose                          |
| ---------------------- | -------------------------------- |
| `download_state`       | Real DownloadState instance      |
| `test_downloads_dir`   | Temporary downloads directory    |
| `DownloadStateFactory` | Factory for custom DownloadState |
| `GlobalStateFactory`   | Factory for GlobalState          |

### Database Fixtures

Located in `tests/fixtures/database/`:

| Fixture        | Purpose                       |
| -------------- | ----------------------------- |
| `session`      | Async database session        |
| `session_sync` | Sync database session         |
| `test_account` | Pre-built Account in database |
| `test_media`   | Pre-built Media in database   |

### Model Factories

Located in `tests/fixtures/metadata/`:

| Factory             | Purpose                    |
| ------------------- | -------------------------- |
| `AccountFactory`    | Create Account entities    |
| `MediaFactory`      | Create Media entities      |
| `PostFactory`       | Create Post entities       |
| `MessageFactory`    | Create Message entities    |
| `AttachmentFactory` | Create Attachment entities |

---

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                          # Shared fixtures
├── fixtures/
│   ├── api/
│   │   └── api_fixtures.py             # API client fixtures
│   ├── download/
│   │   ├── download_fixtures.py        # Download state fixtures
│   │   └── download_factories.py       # Download factories
│   ├── database/
│   │   └── database_fixtures.py        # Database session fixtures
│   └── metadata/
│       └── metadata_factories.py       # SQLAlchemy model factories
├── api/
│   └── unit/
│       ├── test_fansly_api.py          # Main API tests ✅
│       ├── test_fansly_api_additional.py  # Additional tests (migrate)
│       └── test_fansly_api_callback.py # Callback tests ✅
└── download/
    ├── unit/
    │   ├── test_account.py             # Account download tests
    │   ├── test_common.py              # Common download tests
    │   ├── test_m3u8.py                # M3U8 download tests
    │   ├── test_media_filtering.py     # Media filtering tests
    │   ├── test_transaction_recovery.py # Transaction tests
    │   ├── test_downloadstate.py       # State tests ✅
    │   ├── test_globalstate.py         # Global state tests ✅
    │   └── test_pagination_duplication.py  # Pagination tests ✅
    └── integration/
        └── test_m3u8_integration.py    # M3U8 integration tests
```

---

## Migration Progress

### Current Status

Based on `API_DOWNLOAD_TEST_MIGRATION_TODO.md`:

#### API Tests

| File                            | Functions | Violations | Status             | Effort  |
| ------------------------------- | --------- | ---------- | ------------------ | ------- |
| `test_fansly_api.py`            | 35        | 0          | ✅ Compliant       | -       |
| `test_fansly_api_callback.py`   | 1         | 0          | ✅ Compliant       | -       |
| `test_fansly_api_additional.py` | 18        | 5          | ❌ Needs Migration | 2-3 hrs |

**Total API**: 54 tests, 5 violations, **2-3 hours** estimated effort

#### Download Tests

| File                             | Functions | Violations | Status       | Effort  |
| -------------------------------- | --------- | ---------- | ------------ | ------- |
| `test_downloadstate.py`          | 10        | 0          | ✅ Compliant | -       |
| `test_globalstate.py`            | 6         | 0          | ✅ Compliant | -       |
| `test_pagination_duplication.py` | 8         | 0          | ✅ Compliant | -       |
| `test_account.py`                | 18        | 10         | ❌ Medium    | 2-3 hrs |
| `test_common.py`                 | 11        | 9          | ❌ High      | 3-4 hrs |
| `test_media_filtering.py`        | 7         | 5          | ❌ High      | 1-2 hrs |
| `test_transaction_recovery.py`   | 3         | 11         | ❌ Critical  | 2-3 hrs |
| `test_m3u8_integration.py`       | 6         | 16         | ❌ Critical  | 4-5 hrs |
| `test_m3u8.py`                   | 19        | 28         | ❌ Critical  | 6-8 hrs |

**Total Download**: 88 tests, 79 violations, **20-28 hours** estimated effort

### Combined Total

- **142 total tests**
- **84 violations to fix**
- **22-31 hours** estimated effort
- **36% effort reduction** from spy pattern strategy

---

## Key Insights for This Project

### 1. Fansly API Specifics

Unlike GraphQL, Fansly API has:

- **CORS preflight requirements** - Always mock OPTIONS requests
- **Device ID management** - Test both with and without device info
- **Bearer token authentication** - Include in all request assertions
- **Pagination cursors** - Use `before` parameter, not offset/limit
- **Success flag** - Response has `{"success": "true", "response": {...}}`

### 2. Download Pipeline Boundaries

The download pipeline has clear boundaries:

```
External → HTTP (RESPX) → Internal Processing → File System (real tmp_path) → Database (real session)
```

Mock only at:

- ✅ HTTP requests (RESPX)
- ✅ External binaries (subprocess for ffmpeg)

Never mock:

- ❌ Internal download orchestration
- ❌ File system operations
- ❌ Database operations

### 3. M3U8 Complexity

M3U8 tests are the most complex:

- Mock playlist HTTP request
- Mock each segment HTTP request
- Use real temp directory for segments
- Test concatenation logic with real files
- Optional: Mock ffmpeg subprocess

### 4. Test Migration Priority

Follow this order for maximum impact:

1. **Week 1**: API tests (2-3 hrs) + `test_m3u8.py` (6-8 hrs)
2. **Week 2**: `test_m3u8_integration.py` (4-5 hrs) + `test_transaction_recovery.py` (2-3 hrs) + `test_common.py` (3-4 hrs)
3. **Week 3**: `test_account.py` (2-3 hrs) + `test_media_filtering.py` (1-2 hrs)

---

## Reference Documentation

**Related Documents**:

- Migration guide: `API_DOWNLOAD_TEST_MIGRATION_TODO.md`
- Original patterns: `../feat-stash-client-0.8.0-work/TESTING_REQUIREMENTS.md`

**Fixture Definitions**:

- API fixtures: `tests/fixtures/api/api_fixtures.py`
- Download fixtures: `tests/fixtures/download/download_fixtures.py`
- Download factories: `tests/fixtures/download/download_factories.py`
- Database fixtures: `tests/fixtures/database/database_fixtures.py`
- Model factories: `tests/fixtures/metadata/metadata_factories.py`

**External Resources**:

- RESPX Documentation: https://lundberg.github.io/respx/
- Fansly API Documentation: (internal/reverse-engineered)
- Pytest Documentation: https://docs.pytest.org/

**Last Updated**: 2026-01-09
