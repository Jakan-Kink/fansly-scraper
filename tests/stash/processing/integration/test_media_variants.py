"""Tests for media variant and bundle processing functionality."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.fixtures import MediaFactory, MediaLocationFactory


@pytest.mark.asyncio
async def test_process_hls_variant(
    stash_processor,
    mock_media,
    integration_mock_performer,
    integration_mock_account,
    mock_post,
    session_sync,
):
    """Test processing media with HLS stream variant."""
    # Arrange - Create REAL HLS variant Media using factory
    hls_variant = MediaFactory.build(
        id=100102,
        accountId=integration_mock_account.id,
        type=302,  # HLS stream
        mimetype="application/vnd.apple.mpegurl",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    session_sync.add(hls_variant)
    session_sync.commit()

    # Create MediaLocation for the variant
    hls_location = MediaLocationFactory.build(
        mediaId=hls_variant.id,
        locationId=102,
        location="https://example.com/test.m3u8",
    )
    session_sync.add(hls_location)
    session_sync.commit()

    # Add variant to mock_media
    mock_media.variants = {hls_variant}
    mock_media.stash_id = None
    mock_media.is_downloaded = True
    session_sync.add(mock_media)
    session_sync.commit()

    # Mock internal methods to simulate finding media in Stash
    from stash.types import Scene

    mock_scene = MagicMock(spec=Scene)
    mock_scene.id = "scene_123"

    # Mock _find_stash_files_by_path to return a scene
    stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_scene, MagicMock())]
    )
    stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["scenes"]) == 1
    assert result["scenes"][0] == mock_scene
    stash_processor._find_stash_files_by_path.assert_called_once()
    stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_dash_variant(
    stash_processor,
    mock_media,
    integration_mock_performer,
    integration_mock_account,
    mock_post,
    session_sync,
):
    """Test processing media with DASH stream variant."""
    # Arrange - Create REAL DASH variant Media using factory
    dash_variant = MediaFactory.build(
        id=100103,
        accountId=integration_mock_account.id,
        type=303,  # DASH stream
        mimetype="application/dash+xml",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    session_sync.add(dash_variant)
    session_sync.commit()

    # Create MediaLocation for the variant
    dash_location = MediaLocationFactory.build(
        mediaId=dash_variant.id,
        locationId=103,
        location="https://example.com/test.mpd",
    )
    session_sync.add(dash_location)
    session_sync.commit()

    # Add variant to mock_media
    mock_media.variants = {dash_variant}
    mock_media.stash_id = None
    mock_media.is_downloaded = True
    session_sync.add(mock_media)
    session_sync.commit()

    # Mock internal methods to simulate finding media in Stash
    from stash.types import Scene

    mock_scene = MagicMock(spec=Scene)
    mock_scene.id = "scene_123"

    # Mock _find_stash_files_by_path to return a scene
    stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_scene, MagicMock())]
    )
    stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["scenes"]) == 1
    assert result["scenes"][0] == mock_scene
    stash_processor._find_stash_files_by_path.assert_called_once()
    stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_preview_variant(
    stash_processor,
    mock_media,
    integration_mock_performer,
    integration_mock_account,
    mock_post,
    session_sync,
):
    """Test processing media with preview image variant."""
    # Arrange - Create REAL preview variant Media using factory
    preview_variant = MediaFactory.build(
        id=100001,
        accountId=integration_mock_account.id,
        type=1,  # Preview image
        mimetype="image/jpeg",
        meta_info='{"resolutionMode":1}',
        is_downloaded=True,
    )
    session_sync.add(preview_variant)
    session_sync.commit()

    # Create MediaLocation for the variant
    preview_location = MediaLocationFactory.build(
        mediaId=preview_variant.id,
        locationId=1,
        location="https://example.com/preview.jpg",
    )
    session_sync.add(preview_location)
    session_sync.commit()

    # Add variant to mock_media
    mock_media.variants = {preview_variant}
    mock_media.stash_id = None
    mock_media.is_downloaded = True
    session_sync.add(mock_media)
    session_sync.commit()

    # Mock internal methods to simulate finding media in Stash
    from stash.types import Image

    mock_image = MagicMock(spec=Image)
    mock_image.id = "image_123"

    # Mock _find_stash_files_by_path to return an image
    stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_image, MagicMock())]
    )
    stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["images"]) == 1
    assert result["images"][0] == mock_image
    stash_processor._find_stash_files_by_path.assert_called_once()
    stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    stash_processor, mock_media_bundle, integration_mock_performer
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
    stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )
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
    stash_processor, mock_media_bundle, integration_mock_performer
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
    stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )
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
    stash_processor, mock_media_bundle, integration_mock_performer
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
    stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )
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
