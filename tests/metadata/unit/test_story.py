"""Unit tests for metadata/story.py — MediaStory model + process_media_stories."""

import json
import logging
from datetime import datetime

import pytest

from metadata.story import MediaStory, process_media_stories
from tests.fixtures.utils.test_isolation import snowflake_id


class TestMediaStory:
    """Tests for MediaStory model."""

    def test_media_story_init_with_timestamps(self):
        """Test MediaStory initialization with millisecond timestamp conversion."""
        story_id = snowflake_id()
        account_id = snowflake_id()
        content_id = snowflake_id()

        story = MediaStory(
            id=story_id,
            accountId=account_id,
            contentType=1,
            contentId=content_id,
            createdAt=1776270684000,  # milliseconds
            updatedAt=1776270684000,
        )

        assert story.id == story_id
        assert story.accountId == account_id
        assert story.contentType == 1
        assert story.contentId == content_id
        assert isinstance(story.createdAt, datetime)
        assert isinstance(story.updatedAt, datetime)

    def test_media_story_init_without_optional_fields(self):
        """Test MediaStory initialization without optional fields."""
        story_id = snowflake_id()
        account_id = snowflake_id()

        story = MediaStory(
            id=story_id,
            accountId=account_id,
            createdAt=1776270684,
        )

        assert story.id == story_id
        assert story.accountId == account_id
        assert story.contentType is None
        assert story.contentId is None
        assert story.updatedAt is None


class TestProcessMediaStories:
    """Lines 24-76: process_media_stories happy + early-return + missing-accountId."""

    @pytest.mark.asyncio
    async def test_empty_media_stories_returns_empty_list(self, config, entity_store):
        """Line 50: mediaStories empty → return [] without doing anything."""
        result = await process_media_stories(config, {"mediaStories": []})
        assert result == []

    @pytest.mark.asyncio
    async def test_no_media_stories_key_returns_empty_list(self, config, entity_store):
        """Line 48-50: missing mediaStories key → .get default to [] → return []."""
        result = await process_media_stories(config, {})
        assert result == []

    @pytest.mark.asyncio
    async def test_story_without_account_id_emits_json_output_and_skips(
        self, config, entity_store, caplog
    ):
        """Lines 64-70: story_dict without accountId → json_output + continue.

        Asserts via caplog (json_output routes through loguru's json_logger,
        bridged to stdlib via pytest-loguru).
        """
        from metadata.models import Account, AccountMedia, Media

        caplog.set_level(logging.INFO)
        bad_id = snowflake_id()
        good_id = snowflake_id()
        good_account = snowflake_id()
        good_content_id = snowflake_id()
        good_media_id = snowflake_id()

        # MediaStory FK chain: contentId → account_media → media + accounts.
        # Pre-create the whole chain so the good story's save doesn't violate
        # any FK: Media → AccountMedia(mediaId=Media.id) → MediaStory(contentId=AccountMedia.id).
        await entity_store.save(Account(id=good_account, username=f"u_{good_account}"))
        await entity_store.save(
            Media(id=good_media_id, accountId=good_account, createdAt=1776270684)
        )
        await entity_store.save(
            AccountMedia(
                id=good_content_id,
                accountId=good_account,
                mediaId=good_media_id,
                createdAt=1776270684,
            )
        )

        result = await process_media_stories(
            config,
            {
                "mediaStories": [
                    # First story is missing accountId — should be skipped.
                    {
                        "id": bad_id,
                        "contentType": 1,
                        "contentId": snowflake_id(),
                        "createdAt": 1776270684,
                    },
                    # Second story is valid — should be persisted.
                    {
                        "id": good_id,
                        "accountId": good_account,
                        "contentType": 1,
                        "contentId": good_content_id,
                        "createdAt": 1776270684,
                    },
                ],
                "aggregationData": {"accountMedia": []},
            },
        )

        # Only the valid story is persisted.
        assert len(result) == 1
        assert result[0].id == good_id

        # The bad story's storyId surfaces in a json_output INFO record.
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        missing_messages = [m for m in info_messages if "missing_accountId" in m]
        assert len(missing_messages) == 1
        # The payload includes the bad story's id.
        _, _, payload_text = missing_messages[0].partition("\n")
        log_data = json.loads(payload_text)
        assert log_data["storyId"] == bad_id
