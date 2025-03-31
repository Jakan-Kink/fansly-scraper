"""Test pagination duplication detection functionality."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from config import FanslyConfig
from download.common import check_page_duplicates
from errors import DuplicatePageError
from metadata import Account, Base, Post, Wall
from tests.conftest import test_config


@pytest.fixture
def timeline_data():
    """Load sample timeline data."""
    json_path = (
        Path(__file__).parent.parent.parent / "json" / "timeline-sample-account.json"
    )
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)["response"]
        # Make sure we have at least two posts for testing
        if len(data.get("posts", [])) < 2:
            # Create sample posts if not enough in the file
            data["posts"] = [
                {"id": 1001, "accountId": 999},
                {"id": 1002, "accountId": 999},
                {"id": 1003, "accountId": 999},
            ]
        return data


@pytest.fixture
def config(test_config_factory):
    """Create test config with pagination duplication enabled."""
    test_config_factory.use_pagination_duplication = True
    return test_config_factory


# Create a mock account fixture to use for Post creation
@pytest.fixture
async def mock_account_id(test_async_session):
    """Create a mock account ID to use for Post creation."""
    # Check if we need to create a dummy account
    result = await test_async_session.execute(select(Account).filter_by(id=999))
    account = result.scalar_one_or_none()

    if account is None:
        # Create a dummy account to use for tests
        account = Account(
            id=999, username="test_account", createdAt=datetime.now(timezone.utc)
        )
        test_async_session.add(account)
        await test_async_session.commit()

    return 999  # Return the account ID to use


async def test_check_page_duplicates_no_posts(config, test_async_session):
    """Test handling of page data without posts."""
    # Should not raise when no posts array
    await check_page_duplicates(
        config=config,
        page_data={},
        page_type="timeline",
        session=test_async_session,
    )

    # Should not raise when empty posts array
    await check_page_duplicates(
        config=config,
        page_data={"posts": []},
        page_type="timeline",
        session=test_async_session,
    )


async def test_check_page_duplicates_disabled(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test that check is skipped when feature is disabled."""
    config.use_pagination_duplication = False

    # Add all posts to metadata with accountId
    for post in timeline_data["posts"]:
        test_async_session.add(Post(id=post["id"], accountId=mock_account_id))
    await test_async_session.commit()

    # Should not raise even though all posts are in metadata
    await check_page_duplicates(
        config=config,
        page_data=timeline_data,
        page_type="timeline",
        session=test_async_session,
    )


async def test_check_page_duplicates_timeline_new_posts(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test that check passes when new posts are found."""
    # Make a copy of the timeline data to modify
    test_data = deepcopy(timeline_data)

    # Get first post ID for adding to database
    first_post_id = test_data["posts"][0]["id"]

    # Add only first post to metadata
    test_async_session.add(Post(id=first_post_id, accountId=mock_account_id))
    await test_async_session.commit()

    # Verify only one post is in the database
    result = await test_async_session.execute(text("SELECT COUNT(*) FROM posts"))
    count = result.scalar()
    assert count == 1, f"Expected 1 post in database, found {count}"

    # Should not raise since not all posts are in metadata (at least one is new)
    await check_page_duplicates(
        config=config,
        page_data=test_data,
        page_type="timeline",
        cursor="123",
        session=test_async_session,
    )


async def test_check_page_duplicates_timeline_all_existing(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test detection of all posts already in metadata for timeline."""
    # Add all posts to metadata with accountId
    for post in timeline_data["posts"]:
        test_async_session.add(Post(id=post["id"], accountId=mock_account_id))
    await test_async_session.commit()

    # Should raise DuplicatePageError
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="timeline",
            cursor="123",
            session=test_async_session,
        )

    assert "timeline" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_new_posts(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test that check passes when new posts are found on wall."""
    # Make a copy of the timeline data to modify
    test_data = deepcopy(timeline_data)

    # Create wall
    wall = Wall(id=456, name="Test Wall", accountId=mock_account_id)
    test_async_session.add(wall)

    # Get first post ID for adding to database
    first_post_id = test_data["posts"][0]["id"]

    # Add only first post to metadata
    test_async_session.add(Post(id=first_post_id, accountId=mock_account_id))
    await test_async_session.commit()

    # Verify post is in the database
    result = await test_async_session.execute(text("SELECT COUNT(*) FROM posts"))
    count = result.scalar()
    assert count == 1, f"Expected 1 post in database, found {count}"

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=config,
        page_data=test_data,
        page_type="wall",
        page_id=456,
        cursor="123",
        session=test_async_session,
    )


async def test_check_page_duplicates_wall_all_existing(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test detection of all posts already in metadata for wall."""
    # Create wall
    wall = Wall(id=456, name="Test Wall", accountId=mock_account_id)
    test_async_session.add(wall)

    # Add all posts to metadata with accountId
    for post in timeline_data["posts"]:
        test_async_session.add(Post(id=post["id"], accountId=mock_account_id))
    await test_async_session.commit()

    # Should raise DuplicatePageError
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id=456,
            cursor="123",
            session=test_async_session,
        )

    assert "wall" in str(exc_info.value)
    assert "Test Wall" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_no_name(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test wall duplicate detection when wall has no name."""
    # Create wall without name
    wall = Wall(id=456, accountId=mock_account_id)
    test_async_session.add(wall)

    # Add all posts to metadata with accountId
    for post in timeline_data["posts"]:
        test_async_session.add(Post(id=post["id"], accountId=mock_account_id))
    await test_async_session.commit()

    # Should raise DuplicatePageError with just ID
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id=456,
            cursor="123",
            session=test_async_session,
        )

    assert "wall" in str(exc_info.value)
    assert "456" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_nonexistent(
    config, timeline_data, test_async_session, mock_account_id
):
    """Test wall duplicate detection for nonexistent wall."""
    # Add all posts to metadata with accountId
    for post in timeline_data["posts"]:
        test_async_session.add(Post(id=post["id"], accountId=mock_account_id))
    await test_async_session.commit()

    # Should raise DuplicatePageError with just ID
    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            config=config,
            page_data=timeline_data,
            page_type="wall",
            page_id=456,
            cursor="123",
            session=test_async_session,
        )

    assert "wall" in str(exc_info.value)
    assert "456" in str(exc_info.value)
    assert "123" in str(exc_info.value)
