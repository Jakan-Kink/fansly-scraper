"""Tests for common download functionality."""

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import download.media as download_media_mod
from download.common import (
    check_page_duplicates,
    get_unique_media_ids,
    print_download_info,
    process_download_accessible_media,
)
from download.downloadstate import DownloadState
from download.types import DownloadType
from errors import ApiError, DuplicateCountError, DuplicatePageError
from helpers.common import JsonDict
from metadata.models import Account, Media, Post
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.fileio import tiny_jpeg_bytes
from tests.fixtures.utils import scaled_async_sleep
from tests.fixtures.utils.test_isolation import snowflake_id


def _cdn_image_media(account_id: int) -> Media:
    """A Media whose download_url resolves a real CDN image route."""
    return Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype="image/jpeg",
        download_url="https://cdn.fansly.com/content/photo.jpg?Key-Pair-Id=K",
        file_extension="jpg",
    )


def _accessible_state(account_id: int, download_type: DownloadType) -> DownloadState:
    """A DownloadState wired for the real download pipeline."""
    state = DownloadState()
    state.creator_id = account_id
    state.creator_name = f"dl_{account_id}"
    state.download_type = download_type
    return state


@pytest.mark.parametrize(
    ("info_object", "expected_ids", "expected_error"),
    [
        pytest.param(
            {
                "accountMedia": [{"id": 100001}, {"id": 100002}],
                "accountMediaBundles": [
                    {"accountMediaIds": [100002, 100003]},
                    {"accountMediaIds": [100004, 100005]},
                ],
            },
            {100001, 100002, 100003, 100004, 100005},
            None,
            id="duplicates-across-media-and-bundles-deduped",
        ),
        pytest.param(
            {"accountMedia": [], "accountMediaBundles": []},
            set(),
            None,
            id="empty-object-yields-no-ids",
        ),
        pytest.param(
            {"accountMedia": [None], "accountMediaBundles": []},
            None,
            ApiError,
            id="none-media-item-raises-apierror",
        ),
    ],
)
def test_get_unique_media_ids(info_object, expected_ids, expected_error):
    """Extract unique media IDs: dedup across sources, empty input, None item.

    The None-media row is a distinct failure mode — malformed API data
    raises ``ApiError`` instead of returning ids (``expected_error`` column).
    """
    info: JsonDict = info_object
    if expected_error is not None:
        with pytest.raises(expected_error):
            get_unique_media_ids(info)
        return
    unique_ids = get_unique_media_ids(info)
    assert len(unique_ids) == len(expected_ids)
    assert set(unique_ids) == expected_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_pagination_duplication", "page_data"),
    [
        pytest.param(False, {"posts": [{"id": 1}]}, id="disabled-early-return"),
        pytest.param(None, {"posts": []}, id="empty-posts-list"),
        pytest.param(True, {"other_data": "value"}, id="missing-posts-key"),
    ],
)
async def test_check_page_duplicates_early_returns(
    mock_config, download_state, use_pagination_duplication, page_data
):
    """Early-return arms of check_page_duplicates never raise.

    Rows: duplicate checking disabled via config; empty posts list; page_data
    without a "posts" key. ``use_pagination_duplication=None`` leaves the
    fixture default untouched (the empty-posts row never set it).
    """
    if use_pagination_duplication is not None:
        mock_config.use_pagination_duplication = use_pagination_duplication
    data: JsonDict = page_data

    # Should not raise any exceptions (early return)
    await check_page_duplicates(mock_config, data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_wall(mock_config, entity_store):
    """Test duplicate checking for wall pages with new (uncached) post."""
    mock_config.use_pagination_duplication = True
    post_id = snowflake_id()
    page_data: JsonDict = {"posts": [{"id": post_id}]}

    # Post not in store cache → no duplicate → should not raise
    await check_page_duplicates(
        mock_config,
        page_data,
        "wall",
        page_id="wall1",
        cursor="123",
    )


@pytest.mark.asyncio
async def test_check_page_duplicates_all_existing(mock_config, entity_store):
    """Test duplicate checking when all posts exist in store cache."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    post_id = snowflake_id()

    # Pre-populate the store so the post is in the identity map cache
    await entity_store.save(Account(id=account_id, username="dupetest"))
    await entity_store.save(Post(id=post_id, accountId=account_id))

    page_data: JsonDict = {"posts": [{"id": post_id}]}

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", scaled_async_sleep),
    ):
        await check_page_duplicates(mock_config, page_data, "timeline", cursor="123")

    assert exc_info.value.page_type == "timeline"
    assert exc_info.value.cursor == "123"


@pytest.mark.parametrize(
    ("download_type", "expected_threshold"),
    [
        (DownloadType.TIMELINE, 100),  # threshold untouched
        (DownloadType.MESSAGES, 100),  # restored after 0.2*total override
        (DownloadType.WALL, 100),  # restored after max(50, 0.3*len) override
    ],
)
@pytest.mark.asyncio
async def test_process_download_accessible_media_real_pipeline(
    mock_config,
    tmp_path,
    respx_fansly_api,
    entity_store,
    download_type,
    expected_threshold,
):
    """Real per-media pipeline: set_create_directory_for_download + download_media run.

    Merges the former basic / messages / wall shallow arms. ``download_media``
    is an ``AsyncMock(wraps=real_download_media)`` spy — the real CDN pipeline
    executes (real ``set_create_directory_for_download`` against ``tmp_path``,
    real ``process_media_download`` persistence, real ``_download_regular_file``
    streaming the respx-served JPEG, real dedupe), while we still observe the
    spy call. ``imagehash.phash`` is the external-lib leaf boundary.
    """
    mock_config.interactive = False
    mock_config.DUPLICATE_THRESHOLD = 100
    mock_config.download_directory = tmp_path
    mock_config.download_media_previews = False
    mock_config.use_duplicate_threshold = False
    mock_config.separate_timeline = True
    mock_config.separate_messages = True
    mock_config.separate_previews = False
    mock_config.use_folder_suffix = False

    account_id = snowflake_id()
    await entity_store.save(Account(id=account_id, username=f"u_{account_id}"))

    state = _accessible_state(account_id, download_type)
    state.total_message_items = 100
    media = _cdn_image_media(account_id)
    accessible = [media]

    jpeg = tiny_jpeg_bytes()
    cdn_route = respx.get(
        url__startswith="https://cdn.fansly.com/content/photo.jpg"
    ).mock(
        side_effect=[
            httpx.Response(
                200, content=jpeg, headers={"content-length": str(len(jpeg))}
            )
        ]
    )

    original_threshold = mock_config.DUPLICATE_THRESHOLD
    spy = AsyncMock(wraps=download_media_mod.download_media)

    try:
        with (
            patch("download.common.download_media", spy),
            patch("fileio.fnmanip.imagehash.phash", return_value="hash_common"),
        ):
            result = await process_download_accessible_media(
                mock_config, state, accessible
            )
    finally:
        dump_fansly_calls(cdn_route.calls, f"real_pipeline_{download_type.name}")

    assert result is True
    spy.assert_awaited_once_with(mock_config, state, accessible)
    # Real directory wrapper created the on-disk path.
    assert state.download_path is not None
    assert state.download_path.exists()
    # Threshold restored to its pre-call value regardless of arm.
    assert mock_config.DUPLICATE_THRESHOLD == original_threshold == expected_threshold
    # Real download happened: CDN served exactly one image.
    assert cdn_route.called
    assert len(cdn_route.calls) == 1
    assert state.pic_count == 1
    if download_type == DownloadType.MESSAGES:
        # total_message_items grew by the accessible-media count (1 item).
        assert state.total_message_items == 101


@pytest.mark.parametrize(
    ("download_type", "side_effect", "expected_result"),
    [
        # DuplicateCountError on a TIMELINE/WALL download → break (False).
        (DownloadType.TIMELINE, DuplicateCountError(duplicate_count=5), False),
        (DownloadType.WALL, DuplicateCountError(duplicate_count=5), False),
        # A general error is swallowed → keep processing (True).
        (DownloadType.TIMELINE, Exception("Test error"), True),
    ],
)
@pytest.mark.asyncio
async def test_process_download_accessible_media_error_arms(
    mock_config,
    tmp_path,
    download_type,
    side_effect,
    expected_result,
):
    """Error handling arms: DuplicateCountError vs general Exception.

    Merges the former duplicate-error / general-error shallow tests. The error
    is injected on the ``download_media`` spy via ``side_effect`` so the real
    ``process_download_accessible_media`` except/finally logic runs against a
    real ``set_create_directory_for_download`` (real ``tmp_path``).
    """
    mock_config.interactive = False
    mock_config.DUPLICATE_THRESHOLD = 50
    mock_config.download_directory = tmp_path
    mock_config.use_folder_suffix = False

    account_id = snowflake_id()
    state = _accessible_state(account_id, download_type)
    accessible = [_cdn_image_media(account_id)]

    original_threshold = mock_config.DUPLICATE_THRESHOLD
    spy = AsyncMock(wraps=download_media_mod.download_media, side_effect=side_effect)

    with (
        patch("download.common.download_media", spy),
        patch("download.common.input_enter_continue", new_callable=AsyncMock),
    ):
        result = await process_download_accessible_media(mock_config, state, accessible)

    assert result is expected_result
    # Real directory wrapper still ran before download_media was invoked.
    assert state.download_path is not None
    assert state.download_path.exists()
    # Threshold restored in the finally block.
    assert original_threshold == mock_config.DUPLICATE_THRESHOLD


def test_print_download_info(mock_config, caplog):
    """Test download info printing."""
    caplog.set_level(logging.INFO)
    mock_config.user_agent = (
        "Test User Agent String That Is Really Long For Testing Truncation"
    )
    mock_config.open_folder_when_finished = True
    mock_config.download_media_previews = True
    mock_config.interactive = False

    print_download_info(mock_config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]

    expected_user_agent = (
        f"Using user-agent: '{mock_config.user_agent[:28]} [...] "
        f"{mock_config.user_agent[-35:]}'"
    )
    assert expected_user_agent in info_messages
    assert (
        f"Open download folder when finished, is set to: '{mock_config.open_folder_when_finished}'"
        in info_messages
    )
    assert (
        f"Downloading files marked as preview, is set to: '{mock_config.download_media_previews}'"
        in info_messages
    )

    preview_warnings = [
        m for m in warning_messages if "Previews downloading is enabled" in m
    ]
    assert len(preview_warnings) == 1
    assert (
        preview_warnings[0]
        == "Previews downloading is enabled; repetitive and/or emoji spammed media might be downloaded!"
    )
