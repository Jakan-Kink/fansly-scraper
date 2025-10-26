"""Unit tests for metadata.media module."""

import json

import pytest
from sqlalchemy import select

from metadata.media import Media, process_media_item_dict
from tests.fixtures import AccountFactory, MediaFactory


@pytest.mark.asyncio
async def test_media_creation(session, session_sync):
    """Test creating a Media object with basic attributes using MediaFactory.

    Uses MediaFactory to create test data with real database.
    factory_session is autouse=True so it's automatically applied.
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
async def test_process_video_metadata(session, session_sync, config):
    """Test processing video metadata with duration and dimensions.

    Uses real config fixture instead of MagicMock.
    factory_session is autouse=True so it's automatically applied.
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
async def test_media_location(session, session_sync):
    """Test creating and associating MediaLocation with Media.

    Uses MediaFactory and sync session for creation.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account first (foreign key requirement)
    account = AccountFactory(id=123)

    # Create media using factory
    media = MediaFactory(id=1, accountId=123)

    # Create location using sync session
    from tests.fixtures import MediaLocationFactory

    location = MediaLocationFactory(
        mediaId=1, locationId="loc1", location="https://example.com/video.mp4"
    )

    # Commit to ensure data is persisted
    session_sync.commit()

    # Verify in async session with eager loading
    session.expire_all()
    from sqlalchemy.orm import selectinload

    stmt = select(Media).where(Media.id == 1).options(selectinload(Media.locations))
    saved_media = (await session.execute(stmt)).unique().scalar_one_or_none()
    assert len(saved_media.locations) == 1
    assert saved_media.locations["loc1"].location == "https://example.com/video.mp4"


@pytest.mark.asyncio
async def test_invalid_metadata(session, session_sync, config):
    """Test handling invalid metadata JSON.

    Uses real config fixture instead of MagicMock.
    factory_session is autouse=True so it's automatically applied.
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
