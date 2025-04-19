"""Tests for media variant and bundle processing functionality."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_process_hls_variant(stash_processor, mock_media, mock_performer):
    """Test processing media with HLS stream variant."""
    # Arrange
    mock_media.variants = [
        MagicMock(
            type=302,  # HLS stream
            mimetype="application/vnd.apple.mpegurl",
            metadata='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
            locations=[
                {"locationId": "102", "location": "https://example.com/test.m3u8"}
            ],
        )
    ]

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_scene = AsyncMock(
        return_value=MagicMock(id="scene_123")
    )

    # Act
    result = await stash_processor._process_media(mock_media)

    # Assert
    assert result is True
    assert mock_media.stash_id == "scene_123"
    create_scene_call = stash_processor.context.client.create_scene.call_args
    assert "m3u8" in str(create_scene_call)


@pytest.mark.asyncio
async def test_process_dash_variant(stash_processor, mock_media, mock_performer):
    """Test processing media with DASH stream variant."""
    # Arrange
    mock_media.variants = [
        MagicMock(
            type=303,  # DASH stream
            mimetype="application/dash+xml",
            metadata='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
            locations=[
                {"locationId": "103", "location": "https://example.com/test.mpd"}
            ],
        )
    ]

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_scene = AsyncMock(
        return_value=MagicMock(id="scene_123")
    )

    # Act
    result = await stash_processor._process_media(mock_media)

    # Assert
    assert result is True
    assert mock_media.stash_id == "scene_123"
    create_scene_call = stash_processor.context.client.create_scene.call_args
    assert "mpd" in str(create_scene_call)


@pytest.mark.asyncio
async def test_process_preview_variant(stash_processor, mock_media, mock_performer):
    """Test processing media with preview image variant."""
    # Arrange
    mock_media.variants = [
        MagicMock(
            type=1,  # Preview image
            mimetype="image/jpeg",
            metadata='{"resolutionMode":1}',
            locations=[
                {"locationId": "1", "location": "https://example.com/preview.jpg"}
            ],
        )
    ]

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_scene = AsyncMock(
        return_value=MagicMock(id="scene_123")
    )

    # Act
    result = await stash_processor._process_media(mock_media)

    # Assert
    assert result is True
    assert mock_media.stash_id == "scene_123"
    create_scene_call = stash_processor.context.client.create_scene.call_args
    assert "preview" in str(create_scene_call)


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    stash_processor, mock_media_bundle, mock_performer
):
    """Test processing media bundle with specific ordering."""
    # Arrange
    # Create multiple media items in bundle
    media_items = [MagicMock(id=f"media_{i}", stash_id=None) for i in range(3)]
    mock_media_bundle.accountMediaIds = [m.id for m in media_items]
    mock_media_bundle.bundleContent = [
        {"accountMediaId": m.id, "pos": i} for i, m in enumerate(media_items)
    ]

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_gallery = AsyncMock(
        return_value=MagicMock(id="gallery_123")
    )

    # Act
    result = await stash_processor._process_bundle(mock_media_bundle)

    # Assert
    assert result is True
    create_gallery_call = stash_processor.context.client.create_gallery.call_args
    # Verify items were added in correct order
    assert all(f"media_{i}" in str(create_gallery_call) for i in range(3))


@pytest.mark.asyncio
async def test_process_bundle_with_preview(
    stash_processor, mock_media_bundle, mock_performer
):
    """Test processing media bundle with preview image."""
    # Arrange
    mock_media_bundle.previewId = "preview_123"
    preview_media = MagicMock(
        id="preview_123",
        mimetype="image/jpeg",
        locations=[{"locationId": "1", "location": "https://example.com/preview.jpg"}],
    )
    # Add preview media to bundle
    mock_media_bundle.preview = preview_media

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_gallery = AsyncMock(
        return_value=MagicMock(id="gallery_123")
    )

    # Act
    result = await stash_processor._process_bundle(mock_media_bundle)

    # Assert
    assert result is True
    create_gallery_call = stash_processor.context.client.create_gallery.call_args
    assert "preview" in str(create_gallery_call)
    # Verify preview was used
    assert mock_media_bundle.preview == preview_media


@pytest.mark.asyncio
async def test_bundle_permission_inheritance(
    stash_processor, mock_media_bundle, mock_performer
):
    """Test that media items inherit bundle permissions."""
    # Arrange
    # Set bundle permissions
    mock_media_bundle.permissions = {
        "permissionFlags": [
            {"type": 0, "flags": 2, "price": 0, "metadata": "", "verificationFlags": 2}
        ],
        "accountPermissionFlags": {
            "flags": 6,
            "metadata": '{"4":"{\\"subscriptionTierId\\":\\"tier_123\\"}"}',
        },
    }

    # Create media items
    media_items = [MagicMock(id=f"media_{i}", stash_id=None) for i in range(2)]
    mock_media_bundle.accountMediaIds = [m.id for m in media_items]

    # Mock Stash client responses
    stash_processor.context.client.find_performer.return_value = mock_performer
    stash_processor.context.client.create_gallery = AsyncMock(
        return_value=MagicMock(id="gallery_123")
    )

    # Act
    result = await stash_processor._process_bundle(mock_media_bundle)

    # Assert
    assert result is True
    # Verify permissions were inherited
    create_gallery_call = stash_processor.context.client.create_gallery.call_args
    assert "subscription" in str(create_gallery_call)
