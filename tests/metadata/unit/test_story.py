"""Unit tests for metadata/story.py"""

from datetime import datetime

from metadata.story import Story
from tests.fixtures.utils.test_isolation import snowflake_id


class TestStory:
    """Tests for Story model."""

    def test_story_init_with_timestamps(self):
        """Test Story initialization with timestamp conversion."""
        story_id = snowflake_id()
        author_id = snowflake_id()

        # Test with millisecond timestamps
        story = Story(
            id=story_id,
            authorId=author_id,
            content="Story content",
            title="Story Title",
            description="Story description",
            createdAt=1705329000000,  # milliseconds
            updatedAt=1705329100000,  # milliseconds
        )

        assert story.id == story_id
        assert story.authorId == author_id
        assert story.content == "Story content"
        assert story.title == "Story Title"
        assert story.description == "Story description"
        # Timestamps should be converted to datetime objects
        assert isinstance(story.createdAt, datetime)
        assert isinstance(story.updatedAt, datetime)

    def test_story_init_without_optional_fields(self):
        """Test Story initialization without optional fields."""
        story_id = snowflake_id()
        author_id = snowflake_id()

        story = Story(
            id=story_id,
            authorId=author_id,
            content="Story content",
            createdAt=1705329000,
        )

        assert story.id == story_id
        assert story.authorId == author_id
        assert story.content == "Story content"
        assert story.title is None
        assert story.description is None
        assert story.updatedAt is None
