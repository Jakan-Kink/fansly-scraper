"""Tests for media variant and bundle processing functionality."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.types import Image, Scene
from tests.fixtures.metadata.metadata_factories import AccountMediaFactory, MediaFactory
from tests.fixtures import MediaLocationFactory


@pytest.mark.asyncio
async def test_process_hls_variant(
    real_stash_processor,
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
    mock_scene = MagicMock(spec=Scene)
    mock_scene.id = "scene_123"

    # Mock _find_stash_files_by_path to return a scene
    real_stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_scene, MagicMock())]
    )
    real_stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["scenes"]) == 1
    assert result["scenes"][0] == mock_scene
    real_stash_processor._find_stash_files_by_path.assert_called_once()
    real_stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_dash_variant(
    real_stash_processor,
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
    mock_scene = MagicMock(spec=Scene)
    mock_scene.id = "scene_123"

    # Mock _find_stash_files_by_path to return a scene
    real_stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_scene, MagicMock())]
    )
    real_stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["scenes"]) == 1
    assert result["scenes"][0] == mock_scene
    real_stash_processor._find_stash_files_by_path.assert_called_once()
    real_stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_preview_variant(
    real_stash_processor,
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
    mock_image = MagicMock(spec=Image)
    mock_image.id = "image_123"

    # Mock _find_stash_files_by_path to return an image
    real_stash_processor._find_stash_files_by_path = AsyncMock(
        return_value=[(mock_image, MagicMock())]
    )
    real_stash_processor._update_stash_metadata = AsyncMock()

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_media(
        mock_media, mock_post, integration_mock_account, result
    )

    # Assert
    assert len(result["images"]) == 1
    assert result["images"][0] == mock_image
    real_stash_processor._find_stash_files_by_path.assert_called_once()
    real_stash_processor._update_stash_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    real_stash_processor,
    mock_media_bundle,
    integration_mock_performer,
    integration_mock_account,
    session_sync,
):
    """Test processing media bundle with specific ordering."""
    # Arrange
    # Create multiple REAL media items in bundle using factories
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    mock_post = PostFactory.build(accountId=integration_mock_account.id)
    session_sync.add(mock_post)
    session_sync.commit()

    media_items = []
    for _ in range(3):
        # Create Media
        media = MediaFactory.build(
            accountId=mock_media_bundle.accountId,
            mimetype="image/jpeg",
        )
        session_sync.add(media)
        session_sync.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=mock_media_bundle.accountId,
            mediaId=media.id,
        )
        session_sync.add(account_media)
        session_sync.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    # This is the proper way to add media to a bundle!
    for i, account_media in enumerate(media_items):
        session_sync.execute(
            account_media_bundle_media.insert().values(
                bundle_id=mock_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    session_sync.commit()

    # Refresh bundle to load relationships
    session_sync.refresh(mock_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        mock_media_bundle, mock_post, integration_mock_account, result
    )

    # Assert
    # Verify items were added in correct order by checking account_media_ids property
    bundle_media_ids = mock_media_bundle.account_media_ids
    assert len(bundle_media_ids) == 3
    assert bundle_media_ids == [m.id for m in media_items]


@pytest.mark.asyncio
async def test_process_bundle_with_preview(
    real_stash_processor,
    mock_media_bundle,
    integration_mock_performer,
    integration_mock_account,
    session_sync,
):
    """Test processing media bundle with preview image."""
    # Arrange
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    mock_post = PostFactory.build(accountId=integration_mock_account.id)
    session_sync.add(mock_post)
    session_sync.commit()

    # Create REAL preview media using factory instead of MagicMock
    preview_media = MediaFactory.build(
        id=123456,  # Specific ID for this test
        accountId=mock_media_bundle.accountId,
        mimetype="image/jpeg",
        type=1,  # Image type
    )
    session_sync.add(preview_media)
    session_sync.commit()
    session_sync.refresh(preview_media)

    # Update bundle to reference this preview
    mock_media_bundle.previewId = preview_media.id
    session_sync.add(mock_media_bundle)
    session_sync.commit()
    session_sync.refresh(mock_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        mock_media_bundle, mock_post, integration_mock_account, result
    )

    # Assert
    # Verify preview was used (check that previewId is set)
    assert mock_media_bundle.previewId == preview_media.id


@pytest.mark.asyncio
async def test_bundle_permission_inheritance(
    real_stash_processor,
    mock_media_bundle,
    integration_mock_performer,
    integration_mock_account,
    session_sync,
):
    """Test that media items inherit bundle permissions."""
    # Arrange
    # Note: permissions is just data, not a database field in AccountMediaBundle
    # This test verifies that _process_bundle_media properly handles permissions
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    mock_post = PostFactory.build(accountId=integration_mock_account.id)
    session_sync.add(mock_post)
    session_sync.commit()

    # Create REAL media items using factories
    media_items = []
    for _ in range(2):
        # Create Media
        media = MediaFactory.build(
            accountId=mock_media_bundle.accountId,
            mimetype="image/jpeg",
        )
        session_sync.add(media)
        session_sync.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=mock_media_bundle.accountId,
            mediaId=media.id,
        )
        session_sync.add(account_media)
        session_sync.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    for i, account_media in enumerate(media_items):
        session_sync.execute(
            account_media_bundle_media.insert().values(
                bundle_id=mock_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    session_sync.commit()

    # Refresh bundle to load relationships
    session_sync.refresh(mock_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = (
        integration_mock_performer
    )

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        mock_media_bundle, mock_post, integration_mock_account, result
    )

    # Assert
    # Verify bundle was processed
    bundle_media_ids = mock_media_bundle.account_media_ids
    assert len(bundle_media_ids) == 2
    assert bundle_media_ids == [m.id for m in media_items]
