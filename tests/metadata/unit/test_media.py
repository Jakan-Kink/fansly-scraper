"""Unit tests for metadata.media module."""

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from download.downloadstate import DownloadState
from media import MediaItem
from metadata.media import Media, process_media_download, process_media_item_dict
from tests.fixtures import MediaLocationFactory
from tests.fixtures.metadata.metadata_factories import AccountFactory, MediaFactory


@pytest.mark.asyncio
async def test_media_creation(session, session_sync, factory_session):
    """Test creating a Media object with basic attributes using MediaFactory.

    Uses MediaFactory to create test data with real database.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account first (foreign key requirement)
    account = AccountFactory(id=123)

    # Expire async session to see factory-created data
    session.expire_all()

    # Use factory to create media with specific attributes
    media = MediaFactory(
        id=1,
        accountId=123,
        mimetype="video/mp4",
        width=1920,
        height=1080,
        duration=30.5,
    )

    # Verify in async session
    session.expire_all()
    saved_media = (
        await session.execute(select(Media).where(Media.id == 1))
    ).scalar_one_or_none()
    assert saved_media.id == 1
    assert saved_media.accountId == 123
    assert saved_media.mimetype == "video/mp4"
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_process_video_metadata(session, session_sync, config, factory_session):
    """Test processing video metadata with duration and dimensions.

    Uses real config fixture instead of MagicMock.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account first (foreign key requirement)
    account = AccountFactory(id=123)
    session.expire_all()

    media_item = {
        "id": 1,
        "accountId": 123,
        "mimetype": "video/mp4",
        "metadata": json.dumps(
            {"original": {"width": 1920, "height": 1080}, "duration": 30.5}
        ),
    }

    await process_media_item_dict(config, media_item, session=session)

    session.expire_all()
    saved_media = (
        await session.execute(select(Media).where(Media.id == 1))
    ).scalar_one_or_none()
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_media_location(session, session_sync, factory_session):
    """Test creating and associating MediaLocation with Media.

    Uses MediaFactory and sync session for creation.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account first (foreign key requirement)
    account = AccountFactory(id=123)

    # Create media using factory
    media = MediaFactory(id=1, accountId=123)

    # Create location using sync session - locationId must be integer
    location = MediaLocationFactory(
        mediaId=1, locationId=102, location="https://example.com/video.mp4"
    )

    # Commit to ensure data is persisted
    session_sync.commit()

    # Verify in async session with eager loading
    session.expire_all()
    stmt = select(Media).where(Media.id == 1).options(selectinload(Media.locations))
    saved_media = (await session.execute(stmt)).unique().scalar_one_or_none()
    assert len(saved_media.locations) == 1
    assert saved_media.locations[102].location == "https://example.com/video.mp4"


@pytest.mark.asyncio
async def test_invalid_metadata(session, session_sync, config, factory_session):
    """Test handling invalid metadata JSON.

    Uses real config fixture instead of MagicMock.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account first (foreign key requirement)
    account = AccountFactory(id=123)
    session.expire_all()

    media_item = {
        "id": 1,
        "accountId": 123,
        "mimetype": "video/mp4",
        "metadata": "invalid json",
    }

    await process_media_item_dict(config, media_item, session=session)

    session.expire_all()
    saved_media = (
        await session.execute(select(Media).where(Media.id == 1))
    ).scalar_one_or_none()
    assert saved_media.meta_info == "invalid json"
    assert saved_media.duration is None
    assert saved_media.width is None
    assert saved_media.height is None


@pytest.mark.asyncio
async def test_process_media_download_creates_variant_junction(
    session, session_sync, config, factory_session
):
    """Test that process_media_download inserts a media_variants junction row.

    When a MediaItem has media_id != default_normal_id (i.e., a variant was
    selected for download), the function should insert a row linking the
    primary media to the variant in the media_variants junction table.
    """
    primary_id = 1000
    variant_id = 2000
    account_id = 123

    # Create account and both primary + variant media records (FK requirement)
    AccountFactory(id=account_id)
    MediaFactory(id=primary_id, accountId=account_id, mimetype="video/mp4")
    MediaFactory(id=variant_id, accountId=account_id, mimetype="video/mp4")
    session.expire_all()

    # Build a MediaItem where the download picked a variant
    media_item = MediaItem(
        media_id=variant_id,
        default_normal_id=primary_id,
        mimetype="video/mp4",
        created_at=1700000000,
    )

    state = DownloadState()
    state.creator_id = str(account_id)
    state.creator_name = "test_creator"

    result = await process_media_download(config, state, media_item, session=session)

    # The Media record for the variant should be returned
    assert result is not None
    assert result.id == variant_id

    # Verify the junction row was created
    await session.flush()
    junction_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM media_variants "
            'WHERE "mediaId" = :primary_id AND "variantId" = :variant_id'
        ),
        {"primary_id": primary_id, "variant_id": variant_id},
    )
    assert junction_result.scalar() == 1


@pytest.mark.asyncio
async def test_process_media_download_no_junction_when_same_id(
    session, session_sync, config, factory_session
):
    """Test that no junction row is created when media_id == default_normal_id.

    When the primary media is the same as the downloaded media (no variant
    selected), no junction row should be inserted.
    """
    media_id = 3000
    account_id = 123

    AccountFactory(id=account_id)
    MediaFactory(id=media_id, accountId=account_id, mimetype="image/jpeg")
    session.expire_all()

    # MediaItem where media_id == default_normal_id (no variant)
    media_item = MediaItem(
        media_id=media_id,
        default_normal_id=media_id,
        mimetype="image/jpeg",
        created_at=1700000000,
    )

    state = DownloadState()
    state.creator_id = str(account_id)
    state.creator_name = "test_creator"

    await process_media_download(config, state, media_item, session=session)

    # No junction row should exist
    await session.flush()
    junction_result = await session.execute(
        text('SELECT COUNT(*) FROM media_variants WHERE "variantId" = :media_id'),
        {"media_id": media_id},
    )
    assert junction_result.scalar() == 0
