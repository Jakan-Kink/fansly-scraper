"""Tests for download/messages.py — real-orchestration pipeline tests.

Post-Wave-2.4 rewrite: previously every test patched ``process_groups_response``,
``process_messages_metadata``, ``fetch_and_process_media``, and
``process_download_accessible_media`` — tests exercised orchestration
frames but never the real downstream code.

Replacement strategy — only mock at true edges:
- Fansly HTTP (``respx.mock`` via ``respx_fansly_api``)
- ``download_media`` — the CDN leaf (patched at BOTH ``download.common``
  and ``download.media`` because common.py imports it at module scope).
- ``asyncio.sleep`` (``download.messages.sleep``) — no wall-time pauses
- ``input_enter_continue`` across every module that imports it

Everything above the CDN leaf runs real code:
``process_groups_response``, ``process_messages_metadata``,
``fetch_and_process_media``, ``process_download_accessible_media`` — all
persist to the real PostgreSQL database backing ``entity_store``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from download.downloadstate import DownloadState
from download.messages import download_messages, download_messages_for_group
from download.types import DownloadType
from metadata import Account, Message
from metadata.models import get_store
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


def _groups_response(
    groups: list[dict] | None = None,
    accounts: list[dict] | None = None,
) -> dict:
    """Build a plausible ``/api/v1/messaging/groups`` response envelope.

    Groups reference accounts via ``createdBy`` + ``users[].userId`` FKs.
    ``process_groups_response`` processes ``aggregationData.accounts``
    BEFORE groups so they're in the identity map when the group-side FK
    validation runs — tests that include groups MUST also include their
    referenced accounts in this list.
    """
    return {
        "success": True,
        "response": {
            "data": [],
            "aggregationData": {
                "groups": groups or [],
                "accounts": accounts or [],
            },
        },
    }


def _creator_account(creator_id: int, username: str) -> dict:
    """A plausible Account payload for use in groups/aggregation data."""
    return {
        "id": creator_id,
        "username": username,
        "createdAt": 1700000000,
    }


def _messages_response(
    messages: list[dict] | None = None,
    account_media: list[dict] | None = None,
    account_media_bundles: list[dict] | None = None,
) -> dict:
    """Build a plausible ``/api/v1/message`` response envelope."""
    return {
        "success": True,
        "response": {
            "messages": messages or [],
            "accountMedia": account_media or [],
            "accountMediaBundles": account_media_bundles or [],
        },
    }


def _account_media_entry(media_id: int, creator_id: int) -> dict:
    """A plausible AccountMedia payload with nested Media (for FK satisfaction).

    Required fields:
    - ``mediaId`` + nested ``media`` dict: ``process_media_info`` persists
      the nested Media FIRST to satisfy ``account_media_mediaId_fkey``.
    - ``previewId``: ``parse_media_info`` accesses this key unconditionally
      to determine whether to use preview variant (line 92:
      ``is_preview = media_info["previewId"] is not None``). A missing key
      raises KeyError which ``fetch_and_process_media`` silently catches,
      leaving the media out of ``accessible_media``.
    - ``media.locations[].location``: becomes ``Media.download_url`` via
      ``_get_best_location_url``. **Must include ``Key-Pair-Id``** —
      ``parse_media_info`` at media/media.py:177-185 calls raw ``input()``
      (not via ``input_enter_continue``) when the URL is missing it,
      which raises EOFError in a test environment and is caught by
      ``fetch_and_process_media``'s silent exception handler — producing
      empty accessible_media. Real Fansly CDN URLs always include this.
    """
    return {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "previewId": None,
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
                    "location": (
                        "https://cdn.example.com/img.jpg"
                        "?Policy=abc&Key-Pair-Id=xyz&Signature=def"
                    ),
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_download_messages_success_full_real_pipeline(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Full real pipeline: find creator's DM group → paginate → download.

    Covers lines 24-60 (discovery + group-loop entry) and 128-185 (the
    shared ``_download_group_message_loop``). Only edges patched:
    ``download_media`` (CDN), ``sleep`` (timing), ``input_enter_continue``.
    Everything between — ``process_groups_response`` persisting Group +
    Account rows, ``process_messages_metadata`` persisting Message rows,
    ``fetch_and_process_media`` hitting ``/account/media`` — runs real
    code against the real DB.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.show_downloads = True
    config.show_skipped_downloads = False
    config.interactive = False

    creator_id = snowflake_id()
    group_id = snowflake_id()
    msg_id = snowflake_id()
    media_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"msg_{creator_id}"

    # Two message pages: first has a message, second is empty → IndexError
    # break at ``_download_group_message_loop:183`` (cursor advance).
    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, f"msg_{creator_id}")],
                ),
            )
        ]
    )
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_messages_response(
                    messages=[
                        {
                            "id": msg_id,
                            "senderId": creator_id,
                            "content": "hi",
                            "createdAt": 1700000000,
                            "deleted": False,
                        }
                    ],
                    account_media=[_account_media_entry(media_id, creator_id)],
                ),
            ),
            httpx.Response(200, json=_messages_response()),
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": [_account_media_entry(media_id, creator_id)],
                },
            )
        ]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)
    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    try:
        await download_messages(config, state)
    finally:
        dump_fansly_calls(respx.calls, label="messages_full_pipeline")

    assert state.download_type == DownloadType.MESSAGES

    # Real pipeline persisted the message to DB.

    assert get_store().get_from_cache(Message, msg_id) is not None


@pytest.mark.asyncio
async def test_download_messages_no_group_for_creator_warns_and_exits(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """No group contains the targeted creator → warning log + early return.

    Covers lines 44-58 (the group-finding loop + ``if group_id is None``
    early return).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.interactive = False

    creator_id = snowflake_id()
    other_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = "nochat_creator"

    # The response has a group, but it contains a different user —
    # production code iterates members, never finds creator_id, returns.
    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": snowflake_id(),
                            "createdBy": other_id,
                            "users": [{"userId": other_id}],
                        }
                    ],
                    accounts=[_creator_account(other_id, "other_user")],
                ),
            )
        ]
    )

    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)

    await download_messages(config, state)


@pytest.mark.asyncio
async def test_download_messages_groups_api_non_200_logs_and_returns(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Groups endpoint 4xx → print_error + input_enter_continue + return.

    Covers lines 30-37 (the non-200 groups-response handler).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.interactive = False

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[httpx.Response(403, text="Forbidden")]
    )

    state = DownloadState()
    state.creator_id = snowflake_id()

    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)

    await download_messages(config, state)


@pytest.mark.asyncio
async def test_download_messages_message_page_non_200_logs_and_returns(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Message page returns non-200 → print_error + return (no retry).

    Covers lines 149-156 inside ``_download_group_message_loop``.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.interactive = False

    creator_id = snowflake_id()
    group_id = snowflake_id()

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, f"mfail_{creator_id}")],
                ),
            )
        ]
    )
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[httpx.Response(403, text="Forbidden")]
    )

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"mfail_{creator_id}"

    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)

    await download_messages(config, state)


@pytest.mark.asyncio
async def test_download_messages_for_group_with_creator_info_preset(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """download_messages_for_group: creator_id/name pre-set → use them as-is.

    Covers lines 78-101 of the daemon-path entry (happy path with caller-
    supplied creator context).
    """
    config = mock_config
    config.download_directory = tmp_path
    config.show_downloads = False
    config.interactive = False

    creator_id = snowflake_id()
    group_id = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"dg_{creator_id}"

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, f"dg_{creator_id}")],
                ),
            )
        ]
    )
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[httpx.Response(200, json=_messages_response())]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)
    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_messages_for_group(config, state, group_id)

    assert state.download_type == DownloadType.MESSAGES


@pytest.mark.asyncio
async def test_download_messages_for_group_groups_api_non_200_returns_early(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Non-200 groups fetch in daemon path → print_error + return (no input).

    Covers lines 81-88 — the daemon-variant error path that, unlike
    ``download_messages``, does NOT call ``input_enter_continue`` on
    failure (daemon paths must not block on stdin).
    """
    config = mock_config
    config.download_directory = tmp_path

    # 500 is in httpx_retries' status_forcelist — provide enough responses
    # to exhaust retries.
    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[httpx.Response(500, text="server boom")] * 5
    )

    state = DownloadState()
    state.creator_id = snowflake_id()

    await download_messages_for_group(config, state, snowflake_id())


@pytest.mark.asyncio
async def test_download_messages_for_group_missing_group_warns_and_returns(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Group ID not in response's groups list → warning + early return.

    Covers lines 94-100 — the ``target_group is None`` branch where the
    caller-supplied group_id doesn't match any returned group.
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    known_group_id = snowflake_id()

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": known_group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, "known")],
                ),
            )
        ]
    )

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = "known"

    # Pass a DIFFERENT group_id that's not in the returned list → target_group None.
    missing_group_id = snowflake_id()
    await download_messages_for_group(config, state, missing_group_id)


@pytest.mark.asyncio
async def test_download_messages_for_group_infers_creator_from_group_users(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """state.creator_id=None → inferred from the group's users[] list.

    Covers lines 105-116 — the inference branch that populates
    ``state.creator_id`` from group.users[0].userId when the caller
    didn't supply it (e.g., daemon path where only group_id is known).
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    group_id = snowflake_id()

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, "inferred_user")],
                ),
            )
        ]
    )
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[httpx.Response(200, json=_messages_response())]
    )

    # State starts with NO creator_id — forces inference.
    state = DownloadState()
    assert state.creator_id is None

    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)

    await download_messages_for_group(config, state, group_id)

    # Inference populated creator_id AND (via Account cache lookup) username.
    assert state.creator_id == creator_id
    assert state.creator_name == "inferred_user"


@pytest.mark.asyncio
async def test_download_messages_for_group_infers_creator_id_but_no_account_cached(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """creator_id inferred, but Account isn't in cache → creator_name stays None.

    Covers partial branch 115->118: the ``if account is not None and
    account.username:`` False branch — happens when inference finds a
    user ID but the accounts list in the response was empty (so no
    Account was persisted to cache).
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    group_id = snowflake_id()

    # accounts=[] — creator_id not persisted to identity map → get_from_cache
    # returns None → line 115's ``if account is not None`` is False → fall
    # through to line 118 with creator_name still None.
    # BUT: production code then hits ``if state.creator_id is None: warn +
    # return``. creator_id IS set (inferred), so we continue to the message
    # loop which needs a real response. Pre-create a real Account row via
    # factory so the group FK doesn't violate.

    account = Account.model_validate(
        {"id": creator_id, "username": "", "createdAt": 1700000000}
    )
    await entity_store.save(account)

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    # No accounts in response → no cache entry for creator_id.
                    accounts=[],
                ),
            )
        ]
    )
    # 403 on /message → loop exits at line 149-156 BEFORE reaching
    # set_create_directory_for_download (which would crash on creator_name=None).
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[httpx.Response(403, text="Forbidden")]
    )

    state = DownloadState()
    assert state.creator_id is None
    assert state.creator_name is None

    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)

    await download_messages_for_group(config, state, group_id)

    assert state.creator_id == creator_id
    # Account has empty username, so creator_name inference fails the
    # ``account.username`` truthy check at line 115 → stays None.
    assert state.creator_name is None


@pytest.mark.asyncio
async def test_download_messages_for_group_cannot_identify_creator_warns(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Unparseable users list → state.creator_id stays None → warning + return.

    Covers lines 118-123 — the final ``if state.creator_id is None``
    branch where inference exhausted the user list without finding a
    parseable ID.
    """
    config = mock_config
    config.download_directory = tmp_path

    group_id = snowflake_id()

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": None,
                            # Users list has only unparseable entries — all
                            # three fall through the int/str/ValueError
                            # branches without setting creator_id.
                            "users": [
                                {"userId": None},
                                {"userId": "not-an-integer"},
                                {},  # no userId at all
                            ],
                        }
                    ],
                    accounts=[],
                ),
            )
        ]
    )

    state = DownloadState()
    assert state.creator_id is None

    await download_messages_for_group(config, state, group_id)

    # Inference never ran successfully → creator_id stays None → warn + return.
    assert state.creator_id is None


@pytest.mark.asyncio
async def test_download_messages_skipped_downloads_summary(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Duplicate downloads + show_downloads → "Skipped N" summary log.

    Covers lines 168-177 — the per-page ``skipped_downloads > 1`` branch
    that summarizes duplicate counts. Forces duplicates via a fake
    ``download_media`` that calls ``state.add_duplicate()`` multiple
    times, producing ``skipped_downloads > 1``.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.show_downloads = True
    config.show_skipped_downloads = False
    config.interactive = False

    creator_id = snowflake_id()
    group_id = snowflake_id()
    msg_id = snowflake_id()
    media_id_a = snowflake_id()
    media_id_b = snowflake_id()

    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"skip_{creator_id}"

    respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_groups_response(
                    groups=[
                        {
                            "id": group_id,
                            "createdBy": creator_id,
                            "users": [{"userId": creator_id}],
                        }
                    ],
                    accounts=[_creator_account(creator_id, f"skip_{creator_id}")],
                ),
            )
        ]
    )
    respx.get("https://apiv3.fansly.com/api/v1/message").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_messages_response(
                    messages=[
                        {
                            "id": msg_id,
                            "senderId": creator_id,
                            "content": "m",
                            "createdAt": 1700000000,
                            "deleted": False,
                        }
                    ],
                    account_media=[
                        _account_media_entry(media_id_a, creator_id),
                        _account_media_entry(media_id_b, creator_id),
                    ],
                ),
            ),
            httpx.Response(200, json=_messages_response()),
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": [
                        _account_media_entry(media_id_a, creator_id),
                        _account_media_entry(media_id_b, creator_id),
                    ],
                },
            )
        ]
    )

    # Each download_media call increments state.duplicate_count via the
    # real state.add_duplicate() method — triggering the >1 summary.
    call_records: list[int] = []

    async def _count_as_duplicate(_config, _state, accessible_media):
        call_records.append(len(accessible_media))
        for _ in accessible_media:
            _state.add_duplicate()

    monkeypatch.setattr("download.common.download_media", _count_as_duplicate)
    monkeypatch.setattr("download.media.download_media", _count_as_duplicate)
    monkeypatch.setattr("download.messages.sleep", AsyncMock(return_value=None))
    _noop = lambda _: None  # noqa: E731
    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.messages.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    await download_messages(config, state)

    # Two media items marked as duplicate by the fake CDN → skipped>1.
    assert state.duplicate_count >= 2
    assert sum(call_records) >= 2
