"""Unit tests for metadata/story.py — MediaStory model."""

from datetime import datetime

from metadata.story import MediaStory
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
