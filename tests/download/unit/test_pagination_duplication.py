"""Test pagination duplication detection functionality."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from config import FanslyConfig
from download.common import check_page_duplicates
from errors import DuplicatePageError
from metadata import Post, Wall
from tests.conftest import create_test_config


@pytest.fixture
def timeline_data():
    """Load sample timeline data."""
    json_path = (
        Path(__file__).parent.parent.parent / "json" / "timeline-sample-account.json"
    )
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)["response"]


@pytest.fixture
def config():
    """Create test config with pagination duplication enabled."""
    config = create_test_config()
    config.use_pagination_duplication = True
    return config


async def test_check_page_duplicates_no_posts(config, session):
    """Test handling of page data without posts."""
    # Should not raise when no posts array
    await check_page_duplicates(
        config=config,
        page_data={},
        page_type="timeline",
        session=session,
    )

    # Should not raise when empty posts array
    await check_page_duplicates(
        config=config,
        page_data={"posts": []},
        page_type="timeline",
        session=session,
    )


async def test_check_page_duplicates_disabled(config, timeline_data, session):
    """Test that check is skipped when feature is disabled."""
    config.use_pagination_duplication = False

    # Add all posts to metadata
    for post in timeline_data["posts"]:
        session.add(Post(id=post["id"]))
    await session.commit()

    # Should not raise even though all posts are in metadata
    await check_page_duplicates(
        config=config,
        page_data=timeline_data,
        page_type="timeline",
        session=session,
    )


async def test_check_page_duplicates_timeline_new_posts(config, timeline_data, session):
    """Test that check passes when new posts are found."""
    # Add only first post to metadata
    session.add(Post(id=timeline_data["posts"][0]["id"]))
    await session.commit()

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=config,
        page_data=timeline_data,
        page_type="timeline",
        cursor="123",
        session=session,
    )


async def test_check_page_duplicates_timeline_all_existing(
    config, timeline_data, session
):
    """Test detection of all posts already in metadata for timeline."""
    # Add all posts to metadata
    for post in timeline_data["posts"]:
        session.add(Post(id=post["id"]))
    await session.commit()

    # Should raise DuplicatePageError
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="timeline",
            cursor="123",
            session=session,
        )

    assert "timeline" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_new_posts(config, timeline_data, session):
    """Test that check passes when new posts are found on wall."""
    # Create wall
    wall = Wall(id="456", name="Test Wall")
    session.add(wall)

    # Add only first post to metadata
    session.add(Post(id=timeline_data["posts"][0]["id"]))
    await session.commit()

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=config,
        page_data=timeline_data,
        page_type="wall",
        page_id="456",
        cursor="123",
        session=session,
    )


async def test_check_page_duplicates_wall_all_existing(config, timeline_data, session):
    """Test detection of all posts already in metadata for wall."""
    # Create wall
    wall = Wall(id="456", name="Test Wall")
    session.add(wall)

    # Add all posts to metadata
    for post in timeline_data["posts"]:
        session.add(Post(id=post["id"]))
    await session.commit()

    # Should raise DuplicatePageError
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id="456",
            cursor="123",
            session=session,
        )

    assert "wall" in str(exc_info.value)
    assert "Test Wall" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_no_name(config, timeline_data, session):
    """Test wall duplicate detection when wall has no name."""
    # Create wall without name
    wall = Wall(id="456")
    session.add(wall)

    # Add all posts to metadata
    for post in timeline_data["posts"]:
        session.add(Post(id=post["id"]))
    await session.commit()

    # Should raise DuplicatePageError with just ID
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id="456",
            cursor="123",
            session=session,
        )

    assert "wall" in str(exc_info.value)
    assert "456" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_nonexistent(config, timeline_data, session):
    """Test wall duplicate detection for nonexistent wall."""
    # Add all posts to metadata
    for post in timeline_data["posts"]:
        session.add(Post(id=post["id"]))
    await session.commit()

    # Should raise DuplicatePageError with just ID
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id="456",
            cursor="123",
            session=session,
        )

    assert "wall" in str(exc_info.value)
    assert "456" in str(exc_info.value)
    assert "123" in str(exc_info.value)
