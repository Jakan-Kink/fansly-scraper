"""Unit tests for daemon.runner._handle_*_item branch coverage.

Targets the previously-uncovered exception/unknown-creator branches in:
- _handle_messages_item (lines 222-245)
- _handle_full_creator_item (262-266 unknown, 282-288 exception)
- _handle_redownload_item (303-325 — entirely uncovered)
- _handle_check_access_item (339-359 — entirely uncovered)
- _handle_stories_only_item (376-380 unknown, 391-397 exception)
- _handle_timeline_only_item (429-435 exception)
- _handle_mark_messages_deleted (448-475 — entirely uncovered)

Pattern: each handler reads the same shape — resolve creator name (early
warning + return on None), call download_X, log+re-raise on exception.
The happy paths for full_creator/timeline_only are already covered by
test_runner_wiring.py; this file fills the gaps that loop coverage flagged.

Internal-mock disclosure: tests for the EXCEPTION branches monkeypatch the
download_X functions at the daemon.runner binding. This is at the local
use site (the module under test), the canonical scope for testing
"what happens when the next call raises". Real-pipeline tests for the
happy paths live in test_runner_wiring.py.
"""

from __future__ import annotations

import logging

import pytest

from daemon.handlers import (
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadStoriesOnly,
    DownloadTimelineOnly,
    FullCreatorDownload,
    MarkMessagesDeleted,
    RedownloadCreatorMedia,
)
from daemon.runner import (
    _handle_check_access_item,
    _handle_full_creator_item,
    _handle_mark_messages_deleted,
    _handle_messages_item,
    _handle_redownload_item,
    _handle_stories_only_item,
    _handle_timeline_only_item,
)
from metadata import Message
from tests.fixtures.metadata.metadata_factories import AccountFactory, MessageFactory
from tests.fixtures.utils.test_isolation import snowflake_id


def _logged(caplog, level: str) -> list[str]:
    """Return loguru messages at the given stdlib levelname."""
    return [r.getMessage() for r in caplog.records if r.levelname == level]


# ---------------------------------------------------------------------------
# _handle_messages_item — DownloadMessagesForGroup (lines 208-245)
# ---------------------------------------------------------------------------


class TestHandleMessagesItem:
    """Lines 222-245: _handle_messages_item dispatch + exception bookkeeping."""

    @pytest.mark.asyncio
    async def test_resolves_creator_name_when_sender_id_present(
        self, config, entity_store, monkeypatch, caplog
    ):
        """sender_id provided → calls _resolve_creator_name + populates state."""
        caplog.set_level(logging.INFO)
        sender_id = snowflake_id()
        account = AccountFactory.build(id=sender_id, username="dm_sender")
        await entity_store.save(account)

        captured: list[tuple] = []

        async def _fake_download(_config, state, group_id):
            captured.append((state.creator_name, state.creator_id, group_id))

        monkeypatch.setattr("daemon.runner.download_messages_for_group", _fake_download)

        item = DownloadMessagesForGroup(group_id=200, sender_id=sender_id)
        await _handle_messages_item(config, item)

        assert captured == [("dm_sender", sender_id, 200)]
        info = _logged(caplog, "INFO")
        assert any(
            "downloading messages for group 200" in m and f"sender={sender_id}" in m
            for m in info
        )

    @pytest.mark.asyncio
    async def test_no_sender_id_skips_resolution(
        self, config, entity_store, monkeypatch, caplog
    ):
        """sender_id=None → state.creator_name stays None, no resolve call."""
        caplog.set_level(logging.INFO)

        captured: list[tuple] = []

        async def _fake_download(_config, state, group_id):
            captured.append((state.creator_name, state.creator_id, group_id))

        monkeypatch.setattr("daemon.runner.download_messages_for_group", _fake_download)

        item = DownloadMessagesForGroup(group_id=300, sender_id=None)
        await _handle_messages_item(config, item)

        assert captured == [(None, None, 300)]

    @pytest.mark.asyncio
    async def test_download_exception_re_raised_with_error_log(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 239-245: download_messages_for_group raises → ERROR log + re-raise."""
        caplog.set_level(logging.ERROR)

        async def _raises(*_a, **_k):
            raise RuntimeError("dm download boom")

        monkeypatch.setattr("daemon.runner.download_messages_for_group", _raises)

        item = DownloadMessagesForGroup(group_id=999)
        with pytest.raises(RuntimeError, match="dm download boom"):
            await _handle_messages_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any(
            "download_messages_for_group failed for group 999" in m for m in errors
        )


# ---------------------------------------------------------------------------
# _handle_full_creator_item — gaps (lines 262-266 unknown, 282-288 exception)
# ---------------------------------------------------------------------------


class TestHandleFullCreatorItemBranches:
    """Lines 262-266 + 282-288: unknown-creator + exception branches."""

    @pytest.mark.asyncio
    async def test_unknown_creator_logs_warning_and_returns(
        self, config, entity_store, caplog
    ):
        """Lines 262-266: _resolve_creator_name returns None → warning + return."""
        caplog.set_level(logging.WARNING)

        item = FullCreatorDownload(creator_id=snowflake_id())
        # Account NOT saved → resolve returns None.
        await _handle_full_creator_item(config, item)

        warnings = _logged(caplog, "WARNING")
        assert any(
            "skipping FullCreatorDownload - unknown creator" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_download_exception_re_raised(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 282-288: any download_X raises → ERROR log + re-raise."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="full_creator")
        await entity_store.save(account)

        async def _raises(*_a, **_k):
            raise RuntimeError("full creator boom")

        # First call (get_creator_account_info) raises — covers the exception path.
        monkeypatch.setattr("daemon.runner.get_creator_account_info", _raises)

        item = FullCreatorDownload(creator_id=creator_id)
        with pytest.raises(RuntimeError, match="full creator boom"):
            await _handle_full_creator_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any("FullCreatorDownload failed for full_creator" in m for m in errors)


# ---------------------------------------------------------------------------
# _handle_redownload_item — entirely uncovered (lines 303-325)
# ---------------------------------------------------------------------------


class TestHandleRedownloadItem:
    """Lines 291-325: PPV re-download handler — happy + unknown + exception."""

    @pytest.mark.asyncio
    async def test_unknown_creator_logs_warning(self, config, entity_store, caplog):
        """Lines 304-309: unknown creator → warning + return."""
        caplog.set_level(logging.WARNING)

        item = RedownloadCreatorMedia(creator_id=snowflake_id())
        await _handle_redownload_item(config, item)

        warnings = _logged(caplog, "WARNING")
        assert any(
            "skipping RedownloadCreatorMedia - unknown creator" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_happy_path_calls_three_downloads(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 311-318: account info + timeline + messages all called."""
        caplog.set_level(logging.INFO)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="ppv_creator")
        await entity_store.save(account)

        called: list[str] = []

        async def _info(*_a, **_k):
            called.append("get_creator_account_info")

        async def _tl(*_a, **_k):
            called.append("download_timeline")

        async def _msgs(*_a, **_k):
            called.append("download_messages")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info)
        monkeypatch.setattr("daemon.runner.download_timeline", _tl)
        monkeypatch.setattr("daemon.runner.download_messages", _msgs)

        item = RedownloadCreatorMedia(creator_id=creator_id)
        await _handle_redownload_item(config, item)

        assert called == [
            "get_creator_account_info",
            "download_timeline",
            "download_messages",
        ]
        info = _logged(caplog, "INFO")
        assert any("PPV re-download for ppv_creator" in m for m in info)

    @pytest.mark.asyncio
    async def test_download_exception_re_raised(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 319-325: any download raises → ERROR log + re-raise."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="ppv_err")
        await entity_store.save(account)

        async def _raises(*_a, **_k):
            raise RuntimeError("ppv boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _raises)

        item = RedownloadCreatorMedia(creator_id=creator_id)
        with pytest.raises(RuntimeError, match="ppv boom"):
            await _handle_redownload_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any("RedownloadCreatorMedia failed for ppv_err" in m for m in errors)


# ---------------------------------------------------------------------------
# _handle_check_access_item — entirely uncovered (lines 339-359)
# ---------------------------------------------------------------------------


class TestHandleCheckAccessItem:
    """Lines 328-359: new-follow access check handler."""

    @pytest.mark.asyncio
    async def test_unknown_creator_logs_warning(self, config, entity_store, caplog):
        """Lines 340-345: unknown creator → warning + return."""
        caplog.set_level(logging.WARNING)

        item = CheckCreatorAccess(creator_id=snowflake_id())
        await _handle_check_access_item(config, item)

        warnings = _logged(caplog, "WARNING")
        assert any(
            "skipping CheckCreatorAccess - unknown creator" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_happy_path_calls_account_info_only(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 347-352: just calls get_creator_account_info, nothing else."""
        caplog.set_level(logging.INFO)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="follower_check")
        await entity_store.save(account)

        called: list[str] = []

        async def _info(*_a, **_k):
            called.append("get_creator_account_info")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info)

        item = CheckCreatorAccess(creator_id=creator_id)
        await _handle_check_access_item(config, item)

        assert called == ["get_creator_account_info"]
        info = _logged(caplog, "INFO")
        assert any("checking access for follower_check" in m for m in info)

    @pytest.mark.asyncio
    async def test_account_info_exception_re_raised(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 353-359: get_creator_account_info raises → ERROR + re-raise."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="check_err")
        await entity_store.save(account)

        async def _raises(*_a, **_k):
            raise RuntimeError("check boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _raises)

        item = CheckCreatorAccess(creator_id=creator_id)
        with pytest.raises(RuntimeError, match="check boom"):
            await _handle_check_access_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any("CheckCreatorAccess failed for check_err" in m for m in errors)


# ---------------------------------------------------------------------------
# _handle_stories_only_item — gaps (lines 376-380, 391-397)
# ---------------------------------------------------------------------------


class TestHandleStoriesOnlyItemBranches:
    """Lines 374-397: stories-only handler unknown + exception branches."""

    @pytest.mark.asyncio
    async def test_unknown_creator_logs_warning(self, config, entity_store, caplog):
        """Lines 376-380: unknown creator → warning + return."""
        caplog.set_level(logging.WARNING)

        item = DownloadStoriesOnly(creator_id=snowflake_id())
        await _handle_stories_only_item(config, item)

        warnings = _logged(caplog, "WARNING")
        assert any(
            "skipping DownloadStoriesOnly - unknown creator" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_download_exception_re_raised(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 391-397: download_stories raises → ERROR + re-raise."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="story_err")
        await entity_store.save(account)

        async def _info_ok(*_a, **_k):
            return None

        async def _raises(*_a, **_k):
            raise RuntimeError("stories boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info_ok)
        monkeypatch.setattr("daemon.runner.download_stories", _raises)

        item = DownloadStoriesOnly(creator_id=creator_id)
        with pytest.raises(RuntimeError, match="stories boom"):
            await _handle_stories_only_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any("DownloadStoriesOnly failed for story_err" in m for m in errors)


# ---------------------------------------------------------------------------
# _handle_timeline_only_item — gap (lines 429-435 exception)
# ---------------------------------------------------------------------------


class TestHandleTimelineOnlyItemException:
    """Lines 429-435: timeline-only handler exception branch."""

    @pytest.mark.asyncio
    async def test_download_exception_re_raised(
        self, config, entity_store, monkeypatch, caplog
    ):
        """download_timeline raises → ERROR log + re-raise."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()
        account = AccountFactory.build(id=creator_id, username="tl_err")
        await entity_store.save(account)

        async def _info_ok(*_a, **_k):
            return None

        async def _raises(*_a, **_k):
            raise RuntimeError("timeline boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info_ok)
        monkeypatch.setattr("daemon.runner.download_timeline", _raises)

        item = DownloadTimelineOnly(creator_id=creator_id)
        with pytest.raises(RuntimeError, match="timeline boom"):
            await _handle_timeline_only_item(config, item)

        errors = _logged(caplog, "ERROR")
        assert any(
            f"timeline-only download failed for {creator_id}" in m for m in errors
        )


# ---------------------------------------------------------------------------
# _handle_mark_messages_deleted — entirely uncovered (lines 448-475)
# ---------------------------------------------------------------------------


class TestHandleMarkMessagesDeleted:
    """Lines 438-475: mark-deleted handler.

    Iterates message_ids; for each, looks up Message, sets deleted=True +
    deletedAt. Counts missing IDs separately. Falls back to "now" when the
    payload didn't include a deleted_at_epoch.
    """

    @pytest.mark.asyncio
    async def test_all_present_messages_marked_deleted_with_provided_epoch(
        self, config, entity_store, caplog
    ):
        """Lines 451-475: deleted_at_epoch given → use it; all present → all marked."""
        caplog.set_level(logging.INFO)

        # Create 2 real Message rows tied to a real Account + Group.
        sender_id = snowflake_id()
        sender = AccountFactory.build(id=sender_id, username="msg_sender")
        await entity_store.save(sender)

        from tests.fixtures.metadata.metadata_factories import GroupFactory

        group_id = snowflake_id()
        group = GroupFactory.build(id=group_id, createdBy=sender_id)
        await entity_store.save(group)

        msg_ids = [snowflake_id(), snowflake_id()]
        for mid in msg_ids:
            msg = MessageFactory.build(
                id=mid, senderId=sender_id, groupId=group_id, deleted=False
            )
            await entity_store.save(msg)

        # epoch = 2026-04-01 12:00:00 UTC = 1774008000
        epoch = 1774008000
        item = MarkMessagesDeleted(message_ids=tuple(msg_ids), deleted_at_epoch=epoch)

        await _handle_mark_messages_deleted(config, item)

        # Both messages now flagged deleted with the provided timestamp.
        for mid in msg_ids:
            stored = await entity_store.get(Message, mid)
            assert stored is not None
            assert stored.deleted is True
            assert stored.deletedAt is not None
            assert int(stored.deletedAt.timestamp()) == epoch

        info = _logged(caplog, "INFO")
        assert any(
            "marked 2 message(s) deleted (0 not in local archive)" in m for m in info
        )

    @pytest.mark.asyncio
    async def test_missing_message_counted_separately(
        self, config, entity_store, caplog
    ):
        """Lines 465-466: store.get returns None → counted as missing."""
        caplog.set_level(logging.INFO)

        # Create only 1 message; reference 3 IDs so 2 are missing.
        sender_id = snowflake_id()
        sender = AccountFactory.build(id=sender_id, username="partial_sender")
        await entity_store.save(sender)

        from tests.fixtures.metadata.metadata_factories import GroupFactory

        group_id = snowflake_id()
        await entity_store.save(GroupFactory.build(id=group_id, createdBy=sender_id))

        present_id = snowflake_id()
        await entity_store.save(
            MessageFactory.build(
                id=present_id, senderId=sender_id, groupId=group_id, deleted=False
            )
        )
        missing_ids = (snowflake_id(), snowflake_id())

        item = MarkMessagesDeleted(
            message_ids=(present_id, *missing_ids), deleted_at_epoch=None
        )
        await _handle_mark_messages_deleted(config, item)

        info = _logged(caplog, "INFO")
        # 1 marked, 2 missing.
        assert any(
            "marked 1 message(s) deleted (2 not in local archive)" in m for m in info
        )

    @pytest.mark.asyncio
    async def test_no_epoch_falls_back_to_now(self, config, entity_store, caplog):
        """Lines 451-454: deleted_at_epoch=None → fallback to datetime.now(UTC)."""
        caplog.set_level(logging.INFO)
        from datetime import UTC, datetime

        sender_id = snowflake_id()
        await entity_store.save(
            AccountFactory.build(id=sender_id, username="now_sender")
        )

        from tests.fixtures.metadata.metadata_factories import GroupFactory

        group_id = snowflake_id()
        await entity_store.save(GroupFactory.build(id=group_id, createdBy=sender_id))

        mid = snowflake_id()
        await entity_store.save(
            MessageFactory.build(
                id=mid, senderId=sender_id, groupId=group_id, deleted=False
            )
        )

        before = datetime.now(UTC)
        item = MarkMessagesDeleted(message_ids=(mid,), deleted_at_epoch=None)
        await _handle_mark_messages_deleted(config, item)
        after = datetime.now(UTC)

        stored = await entity_store.get(Message, mid)
        assert stored.deletedAt is not None
        # The fallback "now" lands within the test's wall-clock window.
        assert before <= stored.deletedAt <= after

    @pytest.mark.asyncio
    async def test_lookup_exception_counted_as_missing(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 459-464: store.get raises → debug log + counted as missing."""
        caplog.set_level(logging.DEBUG)

        # Patch get_store() to return a fake store whose get() raises.
        from metadata.models import get_store as real_get_store

        real_store = real_get_store()

        class _RaisingStore:
            """Wraps the real store but makes get() raise for Message lookups."""

            def __init__(self, real):
                self._real = real

            async def get(self, model, _id):
                if model.__name__ == "Message":
                    raise RuntimeError("simulated db error")
                return await self._real.get(model, _id)

            async def save(self, obj):
                return await self._real.save(obj)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(
            "daemon.runner.get_store", lambda: _RaisingStore(real_store)
        )

        item = MarkMessagesDeleted(message_ids=(snowflake_id(),), deleted_at_epoch=None)
        # Must NOT raise — the inner exception is caught and counted.
        await _handle_mark_messages_deleted(config, item)

        debug = _logged(caplog, "DEBUG")
        assert any("lookup failed" in m and "simulated db error" in m for m in debug)
        info = _logged(caplog, "INFO")
        assert any(
            "marked 0 message(s) deleted (1 not in local archive)" in m for m in info
        )
