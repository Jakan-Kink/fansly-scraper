"""Unit tests for metadata/story.py"""

from datetime import datetime

from metadata.story import Story


class TestStory:
    """Tests for Story model."""

    def test_story_init_with_timestamps(self):
        """Test Story initialization with timestamp conversion."""
        # Test with millisecond timestamps
        story = Story(
            id=12345,
            authorId=67890,
            content="Story content",
            title="Story Title",
            description="Story description",
            createdAt=1705329000000,  # milliseconds
            updatedAt=1705329100000,  # milliseconds
        )

        assert story.id == 12345
        assert story.authorId == 67890
        assert story.content == "Story content"
        assert story.title == "Story Title"
        assert story.description == "Story description"
        # Timestamps should be converted to datetime objects
        assert isinstance(story.createdAt, datetime)
        assert isinstance(story.updatedAt, datetime)

    def test_story_init_without_optional_fields(self):
        """Test Story initialization without optional fields."""
        story = Story(
            id=12345,
            authorId=67890,
            content="Story content",
            createdAt=1705329000,
        )

        assert story.id == 12345
        assert story.authorId == 67890
        assert story.content == "Story content"
        assert story.title is None
        assert story.description is None
        assert story.updatedAt is None
