"""Tests for API response to Stash object mapping functionality."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_map_media_metadata(stash_processor):
    """Test mapping media metadata fields."""
    # Arrange
    media = MagicMock()
    media.metadata = (
        '{"duration":20.667,"frameRate":30,"originalHeight":1080,"originalWidth":1920}'
    )
    media.width = 1920
    media.height = 1080
    media.mimetype = "video/mp4"

    # Act
    metadata = stash_processor._extract_media_metadata(media)

    # Assert
    assert metadata["duration"] == 20.667
    assert metadata["frame_rate"] == 30
    assert metadata["dimensions"] == "1920x1080"


@pytest.mark.asyncio
async def test_map_permission_flags(stash_processor):
    """Test mapping permission flags to tags."""
    # Arrange
    permissions = {
        "permissionFlags": [
            {"type": 0, "flags": 2, "price": 0, "metadata": "", "verificationFlags": 2}
        ],
        "accountPermissionFlags": {
            "flags": 6,
            "metadata": '{"4":"{\\"subscriptionTierId\\":\\"tier_123\\"}"}',
        },
    }

    # Act
    tags = stash_processor._extract_permission_tags(permissions)

    # Assert
    assert "subscription" in tags
    assert any("tier" in tag for tag in tags)


@pytest.mark.asyncio
async def test_map_media_variants(stash_processor):
    """Test mapping media variants to quality options."""
    # Arrange
    variants = [
        MagicMock(
            type=302, metadata='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}'
        ),
        MagicMock(
            type=303, metadata='{"variants":[{"w":1920,"h":1080},{"w":854,"h":480}]}'
        ),
    ]

    # Act
    qualities = stash_processor._extract_variant_qualities(variants)

    # Assert
    assert "1920x1080" in qualities
    assert "1280x720" in qualities
    assert "854x480" in qualities


@pytest.mark.asyncio
async def test_map_bundle_ordering(stash_processor):
    """Test mapping bundle content ordering."""
    # Arrange
    bundle_content = [
        {"accountMediaId": "media_2", "pos": 1},
        {"accountMediaId": "media_1", "pos": 0},
        {"accountMediaId": "media_3", "pos": 2},
    ]

    # Act
    ordered_ids = stash_processor._extract_bundle_order(bundle_content)

    # Assert
    assert ordered_ids == ["media_1", "media_2", "media_3"]


@pytest.mark.asyncio
async def test_map_post_content(stash_processor):
    """Test mapping post content and metadata."""
    # Arrange
    post = MagicMock()
    post.content = "Test post #hashtag @mention"
    post.hashtags = ["hashtag"]
    post.accountMentions = [{"username": "mention"}]
    post.createdAt = datetime(2024, 4, 1, 12, 0, 0)

    # Act
    title, details = stash_processor._extract_post_metadata(post)

    # Assert
    assert "hashtag" in details
    assert "@mention" in details
    assert "2024-04-01" in details


@pytest.mark.asyncio
async def test_map_message_content(stash_processor):
    """Test mapping message content and metadata."""
    # Arrange
    message = MagicMock()
    message.content = "Test message"
    message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    message.group = MagicMock()
    message.group.users = [MagicMock(username="sender")]

    # Act
    title, details = stash_processor._extract_message_metadata(message)

    # Assert
    assert "sender" in title
    assert "2024-04-01" in details
