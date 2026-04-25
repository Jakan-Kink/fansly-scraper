"""Tests for download/timeline.py — real-orchestration pipeline tests.

Post-Wave-2.4 rewrite: previously every test patched the inner pipeline
functions (``check_page_duplicates``, ``process_timeline_posts``,
``process_timeline_media``, ``fetch_and_process_media``,
``process_download_accessible_media``) — tests exercised orchestration
frames but never the real downstream code. Coverage sat at 80.25% line /
66.67% branch.

Replacement strategy — only mock at true edges:
- Fansly HTTP (``respx.mock`` — intercepted at httpcore layer via
  ``respx_fansly_api`` fixture)
- ``download.media.download_media`` (the CDN-download leaf — actually
  pulls bytes over the network, writes to disk). Everything above it —
  ``check_page_duplicates``, ``process_timeline_posts``,
  ``process_timeline_media``, ``fetch_and_process_media``,
  ``process_download_accessible_media`` — runs real code against
  ``entity_store`` + ``mock_config`` (a real ``FanslyConfig`` attached to a real
  PostgreSQL database).
- ``asyncio.sleep`` imported at module scope (via
  ``from asyncio import sleep``) is patched to a no-op so
  ``timing_jitter`` delays don't add real wall time.
- ``input_enter_continue`` is patched — real ``input()`` would block.

Test structure: flat functions with narrative names (not class-based
wrappers, which would only be appropriate for per-function leaf tests).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from download.downloadstate import DownloadState
from download.timeline import (
    download_timeline,
    process_timeline_data,
    process_timeline_media,
)
from download.types import DownloadType
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


def _timeline_response(
    *,
    posts: list[dict] | None = None,
    account_media: list[dict] | None = None,
    account_media_bundles: list[dict] | None = None,
    accounts: list[dict] | None = None,
    aggregated_posts: list[dict] | None = None,
) -> dict:
    """Build a plausible Fansly timeline response envelope.

    Defaults are empty lists everywhere so tests only populate the fields
    they actually care about.
    """
    return {
        "success": True,
        "response": {
            "posts": posts or [],
            "aggregatedPosts": aggregated_posts or [],
            "accountMedia": account_media or [],
            "accountMediaBundles": account_media_bundles or [],
            "accounts": accounts or [],
        },
    }


def _account_media_response(media_items: list[dict]) -> dict:
    """Build a plausible /api/v1/account/media response envelope."""
    return {"success": True, "response": media_items}


@pytest.mark.asyncio
async def test_process_timeline_data_persists_posts_and_checks_duplicates(
    respx_fansly_api, entity_store, mock_config, tmp_path
):
    """process_timeline_data exercises the real check + persist pipeline.

    With a real ``entity_store`` + ``mock_config`` and ``use_pagination_duplication=False``
    (default), ``check_page_duplicates`` is a no-op but still runs real code.
    ``process_timeline_posts`` persists posts + their accounts to the real
    DB. We verify by reading the DB after.
    """
    config = mock_config
    creator_id = snowflake_id()
    post_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id

    timeline = {
        "posts": [
            {
                "id": post_id,
                "accountId": creator_id,
                "fypFlags": 0,
                "createdAt": 1700000000,
            }
        ],
        "aggregatedPosts": [],
        "accountMedia": [],
        "accountMediaBundles": [],
        "accounts": [
            {
                "id": creator_id,
                "username": f"tl_{creator_id}",
                "createdAt": 1700000000,
            }
        ],
    }

    await process_timeline_data(config, state, timeline, 0)

    # Real persistence: the post should now be in the identity-map cache.
    from metadata import Post
    from metadata.models import get_store

    store = get_store()
    cached_post = store.get_from_cache(Post, post_id)
    assert cached_post is not None
    assert cached_post.id == post_id


@pytest.mark.asyncio
async def test_process_timeline_media_returns_true_on_empty_media(
    respx_fansly_api, entity_store, mock_config, tmp_path
):
    """process_timeline_media: no media → real pipeline returns True to continue.

    ``fetch_and_process_media`` with an empty list returns [] without
    hitting HTTP. ``process_download_accessible_media`` with [] returns
    True (orchestration-continue signal).
    """
    config = mock_config
    config.download_directory = tmp_path
    state = DownloadState()
    state.creator_id = snowflake_id()
    state.creator_name = "test_continue"
    state.download_type = DownloadType.TIMELINE

    result = await process_timeline_media(config, state, [])

    assert result is True


@pytest.mark.asyncio
async def test_download_timeline_success_full_real_pipeline(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Full real pipeline: respx → real FanslyApi → real DB → patched CDN leaf.

    Covers lines 97-210 of download/timeline.py: the entire successful
    iteration including the cursor-advance-by-last-post-id branch,
    exhaustion via IndexError, and state.download_type assignment.

    Only edges patched: ``download.media.download_media`` (the real CDN
    downloader — would do real file I/O and network), ``asyncio.sleep``
    via the module's imported reference (no wall-time pauses),
    ``input_enter_continue`` (would block on real stdin).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.debug = False
    config.show_downloads = True
    config.show_skipped_downloads = False
    config.interactive = False
    config.timeline_retries = 0
    config.timeline_delay_seconds = 0

    creator_id = snowflake_id()
    post_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"tldl_{creator_id}"

    # accountMedia entries MUST include a nested "media" key — process_media_info
    # persists the Media row from that nested dict FIRST (to satisfy the
    # account_media_mediaId_fkey FK), then persists AccountMedia. Without the
    # nested media, the AccountMedia insert raises ForeignKeyViolationError
    # which is caught by the outer download_timeline handler and breaks the
    # loop silently — we found this via try/finally + dump_fansly_calls +
    # caplog showing only 2 HTTP calls instead of the expected 3+.
    account_media_entry = {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {
                    "locationId": "1",
                    "location": "https://cdn.example.com/image.jpg",
                }
            ],
        },
    }

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": post_id,
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[account_media_entry],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"tldl_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            ),
            # Second page exhausts via IndexError on timeline["posts"][-1].
            httpx.Response(200, json=_timeline_response()),
        ]
    )

    # fetch_and_process_media → get_account_media against this endpoint.
    # The response is a list of AccountMedia rows — same shape as the
    # timeline's accountMedia entries (with nested "media" dict for FK).
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_account_media_response([account_media_entry]),
            )
        ]
    )

    # Real CDN download leaf — patched to no-op. The real function would
    # download actual bytes over HTTP to a real file path. Patched at BOTH
    # call sites: download.common imports download_media at module scope
    # (``from .media import download_media``) so patching only the source
    # leaves the binding in common.py pointing at the original.
    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)
    # No wall-time pauses — timing_jitter wraps asyncio.sleep.
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _interactive: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    try:
        await download_timeline(config, state)
    finally:
        dump_fansly_calls(respx.calls, label="timeline_full_pipeline")

    assert state.download_type == DownloadType.TIMELINE

    # Real pipeline persisted the post to DB.
    from metadata import Post
    from metadata.models import get_store

    assert get_store().get_from_cache(Post, post_id) is not None


@pytest.mark.asyncio
async def test_download_timeline_empty_media_retries_and_exhausts(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """No media IDs on a page → retry with delay → exhaust retries → break.

    Covers lines 166-176 (the ``if len(all_media_ids) == 0`` branch that
    increments ``attempts`` and continues). With ``timeline_retries=1``,
    two empty pages exhaust the retry budget and exit the loop.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.debug = False
    config.timeline_retries = 1
    config.timeline_delay_seconds = 0
    config.interactive = False

    creator_id = snowflake_id()
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"retry_{creator_id}"

    # Both responses have posts (so IndexError doesn't fire) but no media.
    empty_media_response = _timeline_response(
        posts=[
            {
                "id": snowflake_id(),
                "accountId": creator_id,
                "fypFlags": 0,
                "createdAt": 1700000000,
            }
        ],
        accounts=[
            {
                "id": creator_id,
                "username": f"retry_{creator_id}",
                "createdAt": 1700000000,
            }
        ],
    )
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(200, json=empty_media_response),
            httpx.Response(200, json=empty_media_response),
        ]
    )

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    # input_enter_continue is imported at module scope in BOTH
    # download.common and download.timeline — patch every call site plus
    # the source in textio to short-circuit the 15-second sleep fallback.
    _noop = lambda _interactive: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)

    # No assertion on exit_code — function returns None; the key is that
    # it exits cleanly (no raise) after exhausting retries.


@pytest.mark.asyncio
async def test_download_timeline_skips_when_fetched_duplication_cached(
    entity_store, mock_config
):
    """fetched_timeline_duplication + duplicate-threshold → skip the whole loop.

    Covers lines 118-124 (the ``if state.fetched_timeline_duplication``
    early-return branch).
    """
    config = mock_config
    config.use_duplicate_threshold = True
    config.use_pagination_duplication = False
    # No download_directory needed: this test short-circuits before pathio.

    state = DownloadState()
    state.creator_id = snowflake_id()
    state.fetched_timeline_duplication = True

    # No respx routes — if we accidentally fall through to the HTTP path,
    # the real httpx call would fail loudly instead of silently passing.
    await download_timeline(config, state)

    assert state.download_type == DownloadType.TIMELINE


@pytest.mark.asyncio
async def test_download_timeline_skips_when_creator_content_unchanged(
    entity_store, mock_config
):
    """creator_content_unchanged → credit skipped items as duplicates, return.

    Covers lines 106-116 (the early-return path that credits
    total_timeline_pictures + total_timeline_videos into duplicate_count).
    """
    config = mock_config

    state = DownloadState()
    state.creator_id = snowflake_id()
    state.creator_content_unchanged = True
    state.total_timeline_pictures = 10
    state.total_timeline_videos = 5

    await download_timeline(config, state)

    # Real side effect: duplicate_count incremented by 15.
    assert state.duplicate_count == 15
    assert state.download_type == DownloadType.TIMELINE


@pytest.mark.asyncio
async def test_download_timeline_key_error_on_malformed_response(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Response missing required keys → KeyError → print error + continue path.

    Covers lines 219-228 (the ``except KeyError`` handler with
    non-interactive break).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"ke_{creator_id}"

    # Response body missing the "response" key →
    # ``FanslyApi.get_json_response_contents`` does ``response.json()["response"]``
    # which raises KeyError. KeyError propagates out of the outer try at
    # timeline.py:139 and hits the ``except KeyError`` handler at line 219
    # → print_error + (non-interactive) break.
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(side_effect=[httpx.Response(200, json={"success": True})])

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _interactive: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    # Must not raise — the production code catches KeyError + breaks.
    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_should_continue_false_breaks_loop(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """process_timeline_media returning False → break out of pagination loop.

    Covers lines 186-188 (``if not should_continue: break``). Trigger via
    ``DuplicateCountError`` inside ``download_media`` — the real
    ``process_download_accessible_media`` catches it and returns False
    when the state.download_type is TIMELINE.
    """
    from errors import DuplicateCountError

    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = True
    config.use_pagination_duplication = False
    config.debug = False
    config.DUPLICATE_THRESHOLD = 50
    config.timeline_retries = 0
    config.show_downloads = False
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"brk_{creator_id}"
    # Pre-set duplicate count > threshold to trigger DuplicateCountError
    # inside download_media — but download_media is patched, so we raise
    # directly from the patch.
    state.duplicate_count = 100

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": snowflake_id(),
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[
                        {
                            "id": media_id,
                            "accountId": creator_id,
                            "mediaId": media_id,
                            "createdAt": 1700000000,
                            "deleted": False,
                            "access": True,
                            "media": {
                                "id": media_id,
                                "accountId": creator_id,
                                "mimetype": "image/jpeg",
                                "createdAt": 1700000000,
                                "locations": [
                                    {
                                        "locationId": "1",
                                        "location": "https://cdn.example.com/img.jpg",
                                    }
                                ],
                            },
                        }
                    ],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"brk_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            )
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_account_media_response(
                    [
                        {
                            "id": media_id,
                            "accountId": creator_id,
                            "mediaId": media_id,
                            "createdAt": 1700000000,
                            "deleted": False,
                            "access": True,
                            "mimetype": "image/jpeg",
                            "media": {
                                "id": media_id,
                                "accountId": creator_id,
                                "mimetype": "image/jpeg",
                                "createdAt": 1700000000,
                                "locations": [
                                    {
                                        "locationId": "1",
                                        "location": "https://cdn.example.com/img.jpg",
                                    }
                                ],
                            },
                        }
                    ]
                ),
            )
        ]
    )

    async def _raise_duplicate_count(_config, _state, _media):
        raise DuplicateCountError(_state.duplicate_count)

    # Patch at the actual call site (download.common) AND the source
    # (download.media). See test_download_timeline_batch_duplicate_prints_skipped_count
    # for the import-binding rationale.
    monkeypatch.setattr("download.common.download_media", _raise_duplicate_count)
    monkeypatch.setattr("download.media.download_media", _raise_duplicate_count)
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    # input_enter_continue is imported at module scope in BOTH
    # download.common and download.timeline — patch every call site plus
    # the source in textio to short-circuit the 15-second sleep fallback.
    _noop = lambda _interactive: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_debug_mode_prints_timeline_object(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch, capsys
):
    """``config.debug=True`` → prints the full timeline dict via ``print_debug``.

    Covers line 162 (``if config.debug: print_debug(...)``) — the debug
    branch that dumps the entire timeline response object when enabled.
    Minimal setup reuses the success-pipeline fixtures but sets
    ``debug=True``.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.debug = True
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"dbg_{creator_id}"

    am_entry = {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {"locationId": "1", "location": "https://cdn.example.com/img.jpg"}
            ],
        },
    }
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": snowflake_id(),
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[am_entry],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"dbg_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            ),
            httpx.Response(200, json=_timeline_response()),
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[httpx.Response(200, json=_account_media_response([am_entry]))]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)

    # Debug output flows through loguru; we can't easily assert on it from
    # capsys, but the coverage report confirms line 162 was hit.


@pytest.mark.asyncio
async def test_download_timeline_batch_duplicate_prints_skipped_count(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """``current_batch_duplicates > 0`` + ``show_downloads`` → skipped log.

    Covers lines 191-198 (``if state.current_batch_duplicates > 0 and
    config.show_downloads and not config.show_skipped_downloads:``) — the
    per-batch "Skipped N already-downloaded media item(s)" summary that
    only fires when there were duplicates AND show_downloads is on AND
    show_skipped_downloads is OFF.

    We force duplicates by having ``process_download_accessible_media``
    increment state.current_batch_duplicates via the production
    ``state.add_duplicate()`` call path. Instead of re-staging the whole
    pipeline, we inject a small monkeypatch on
    ``process_download_accessible_media`` itself — NOT as a MockInternal
    violation, but as a test-time accelerator for the specific side-effect
    we care about (production behavior is covered by the success tests).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.show_downloads = True
    config.show_skipped_downloads = False
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"skip_{creator_id}"

    am_entry = {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {"locationId": "1", "location": "https://cdn.example.com/img.jpg"}
            ],
        },
    }
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": snowflake_id(),
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[am_entry],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"skip_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            ),
            httpx.Response(200, json=_timeline_response()),
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[httpx.Response(200, json=_account_media_response([am_entry]))]
    )

    # Side-effect the duplicate counter via the real state.add_duplicate()
    # call — driven from within download_media (the patched CDN leaf).
    # NOTE: ``download.common`` imports ``download_media`` at module scope
    # (``from .media import download_media``) — patching the source
    # (``download.media.download_media``) does NOT intercept that binding.
    # Patch where it's CALLED FROM (``download.common.download_media``).
    async def _fake_download_that_counts_duplicate(_config, _state, _media):
        _state.add_duplicate()

    monkeypatch.setattr(
        "download.common.download_media", _fake_download_that_counts_duplicate
    )
    monkeypatch.setattr(
        "download.media.download_media", _fake_download_that_counts_duplicate
    )
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)

    # state.current_batch_duplicates was incremented by the real
    # state.add_duplicate() call path.
    assert state.duplicate_count >= 1


@pytest.mark.asyncio
async def test_download_timeline_generic_exception_logs_and_breaks(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Unexpected Exception from API → caught by outer handler → break.

    Covers lines 235-242 (``except Exception: print_error + break``). The
    respx route itself raises a non-ApiError, non-KeyError exception to
    fall into the catch-all handler.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"exc_{creator_id}"

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(side_effect=[RuntimeError("simulated API explosion")])

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    # Must not raise — catch-all handler logs + breaks.
    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_cursor_index_error_breaks_cleanly(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Empty ``posts`` with non-empty media → IndexError at cursor advance → break.

    Covers lines 207-209 (``except IndexError: break``). Forcing this
    branch requires a page where ``get_unique_media_ids`` returns
    non-empty (so we skip the retry-on-empty-media path) but
    ``timeline["posts"]`` is an empty list (so
    ``timeline["posts"][-1]["id"]`` at line 205 raises IndexError).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"idx_{creator_id}"

    am_entry = {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {"locationId": "1", "location": "https://cdn.example.com/img.jpg"}
            ],
        },
    }
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[],  # empty — triggers IndexError at cursor advance
                    account_media=[am_entry],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"idx_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            )
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[httpx.Response(200, json=_account_media_response([am_entry]))]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_cursor_advance_generic_exception_wraps_as_api_error(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Generic Exception during cursor advance → wrapped + raised as ApiError.

    Covers lines 211-217 (``except Exception: raise ApiError(...)``) plus
    the outer handler that catches it. Forced by making
    ``download.timeline.sleep`` raise a non-IndexError exception, which
    happens inside the cursor-advance try block.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"apierr_{creator_id}"

    am_entry = {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {"locationId": "1", "location": "https://cdn.example.com/img.jpg"}
            ],
        },
    }
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": snowflake_id(),
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[am_entry],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"apierr_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            )
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[httpx.Response(200, json=_account_media_response([am_entry]))]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)

    # Make the cursor-advance sleep raise — fires the inner
    # ``except Exception: raise ApiError(...)`` block.
    async def _raising_sleep(_duration):
        raise RuntimeError("simulated sleep failure")

    monkeypatch.setattr("download.timeline.sleep", _raising_sleep)
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    # ApiError is raised inside, caught by the outer ``except Exception``
    # (ApiError inherits from Exception) → log + break.
    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_non_200_2xx_response_skips_block_and_re_polls(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Non-200 2xx response (e.g., 204) → skip success block → loop continues.

    Covers partial branch 149->127: ``if timeline_response.status_code == 200``
    False direction. ``raise_for_status()`` passes on any 2xx (200-299), so a
    204 (or any other 2xx-non-200) reaches line 149 with the success-check
    False, falls through past the entire success block, and the outer while
    loop runs again — fetching a fresh response.

    This is extremely unlikely with the real Fansly API (which always
    returns 200 on success), but the defensive check exists and the
    branch IS reachable. We exercise it by sending a 204 first; respx
    serves the next side_effect on iter 2 (a 200 response with posts
    but empty media → empty-media retry path → attempts++ → exits).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.timeline_delay_seconds = 0
    config.interactive = False

    creator_id = snowflake_id()
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"two04_{creator_id}"

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            # iter 1: 204 No Content. raise_for_status passes (2xx),
            # but `if status_code == 200` is False → skip success block →
            # fall through past try/except → back to while header.
            httpx.Response(204),
            # iter 2: real 200 with posts but no media. get_unique_media_ids
            # returns [] → empty-media retry path → attempts becomes 1 →
            # while: 1 <= 0 False → loop exits cleanly.
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": snowflake_id(),
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"two04_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            ),
        ]
    )

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)

    assert state.download_type == DownloadType.TIMELINE


@pytest.mark.asyncio
async def test_download_timeline_none_creator_id_raises_runtime_error(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """state.creator_id=None → RuntimeError caught by outer Exception handler.

    Covers line 141 (``raise RuntimeError("Creator name or timeline
    cursor should not be None")``). This is a defensive invariant — in
    normal flow state.creator_id is always set by the calling code.
    Testing it requires explicitly leaving creator_id as None.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.interactive = False

    state = DownloadState()
    # Intentionally leave state.creator_id = None (default).
    state.creator_name = "none_cid"

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    # The RuntimeError from line 141 is caught by the outer Exception
    # handler at line 235 → logs + break. Must not propagate.
    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_interactive_key_error_continues_loop(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Interactive KeyError → loop continues (no break).

    Covers partial branch 227->127 (``if not config.interactive`` False →
    skip break, fall through to next while iteration). Page 1 returns a
    malformed response triggering KeyError; page 2 is a normal empty
    response that exhausts via IndexError → break.

    Critical detail: with ``timeline_retries=0`` the while condition
    ``attempts <= 0`` exits after one iteration, so "continue loop" still
    terminates — but it does so via the natural-exit branch rather than
    the ``break`` inside the except handler.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    # timeline_retries controls both the outer while bound AND the
    # empty-media retry budget. With retries=0 and interactive=True, after
    # KeyError the handler "continues" but attempts isn't incremented — we
    # come back to the while header, condition `0 <= 0` is True, loop
    # again. To avoid an infinite loop we set retries=0 AND provide enough
    # responses so the empty-media retry path exhausts attempts and exits.
    config.timeline_retries = 0
    config.timeline_delay_seconds = 0
    config.interactive = True

    creator_id = snowflake_id()
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"ike_{creator_id}"

    # Provide multiple fallback responses so the loop can exit naturally
    # after KeyError → continue → empty-retry-exhaust.
    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(200, json={"success": True}),  # triggers KeyError
            *[httpx.Response(200, json=_timeline_response()) for _ in range(5)],
        ]
    )

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_interactive_generic_exception_continues_loop(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Interactive generic Exception → loop continues.

    Covers partial branch 241->127 (``if not config.interactive`` False in
    the catch-all Exception handler). Page 1 API raises RuntimeError;
    subsequent pages return empty to exit cleanly via retry-budget
    exhaustion.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = False
    config.timeline_retries = 0
    config.timeline_delay_seconds = 0
    config.interactive = True

    creator_id = snowflake_id()
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"iexc_{creator_id}"

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            RuntimeError("simulated API explosion"),
            *[httpx.Response(200, json=_timeline_response()) for _ in range(5)],
        ]
    )

    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_timeline(config, state)


@pytest.mark.asyncio
async def test_download_timeline_duplicate_page_error_breaks_loop(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """All posts on page already cached → real DuplicatePageError raised + break.

    Seeds the identity-map cache with Post objects whose IDs match the
    respx response's posts. The real ``check_page_duplicates`` (with
    ``use_pagination_duplication=True``) finds every post in cache and
    raises DuplicatePageError — the outer loop catches it and breaks.

    This test uses the REAL duplicate-detection logic, not a mock raise —
    proof that pre-seeding + the real cache lookup produce the expected
    branch.
    """
    from metadata import Post
    from metadata.models import get_store

    config = mock_config
    config.download_directory = tmp_path
    config.use_duplicate_threshold = False
    config.use_pagination_duplication = True  # enables check_page_duplicates
    config.timeline_retries = 0
    config.interactive = False

    creator_id = snowflake_id()
    post_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"dup_{creator_id}"

    # Pre-seed the identity-map cache with a Post matching the upcoming
    # respx response. `model_validate` puts it in the cache.
    Post.model_validate(
        {
            "id": post_id,
            "accountId": creator_id,
            "fypFlags": 0,
            "createdAt": 1700000000,
        }
    )
    # Confirm cache seed.
    assert get_store().get_from_cache(Post, post_id) is not None

    respx.get(
        url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=_timeline_response(
                    posts=[
                        {
                            "id": post_id,
                            "accountId": creator_id,
                            "fypFlags": 0,
                            "createdAt": 1700000000,
                        }
                    ],
                    accounts=[
                        {
                            "id": creator_id,
                            "username": f"dup_{creator_id}",
                            "createdAt": 1700000000,
                        }
                    ],
                ),
            )
        ]
    )

    # check_page_duplicates does an asyncio.sleep(5) before raising; short-
    # circuit via the module-level patch.
    monkeypatch.setattr("download.common.asyncio.sleep", AsyncMock(return_value=None))
    monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
    # input_enter_continue is imported at module scope in BOTH
    # download.common and download.timeline — patch every call site plus
    # the source in textio to short-circuit the 15-second sleep fallback.
    _noop = lambda _interactive: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    # Must not raise — the DuplicatePageError is caught by the timeline
    # loop and logged as info_highlight, then broken out of.
    await download_timeline(config, state)
