"""Test pagination duplication detection functionality."""

from unittest.mock import AsyncMock, patch

import pytest

from download.common import check_page_duplicates
from errors import DuplicatePageError
from metadata.models import Account, Post, Wall
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture
def timeline_data():
    """Generate timeline-like page data with multiple posts."""
    account_id = snowflake_id()
    return {
        "posts": [
            {"id": snowflake_id(), "accountId": account_id},
            {"id": snowflake_id(), "accountId": account_id},
            {"id": snowflake_id(), "accountId": account_id},
        ],
    }


async def test_check_page_duplicates_no_posts(mock_config, entity_store):
    """Test handling of page data without posts."""
    mock_config.use_pagination_duplication = True

    # Should not raise when no posts key
    await check_page_duplicates(
        config=mock_config,
        page_data={},
        page_type="timeline",
    )

    # Should not raise when empty posts array
    await check_page_duplicates(
        config=mock_config,
        page_data={"posts": []},
        page_type="timeline",
    )


async def test_check_page_duplicates_disabled(mock_config, entity_store, timeline_data):
    """Test that check is skipped when feature is disabled."""
    mock_config.use_pagination_duplication = False

    account_id = snowflake_id()
    account = Account(id=account_id, username="disabled_test")
    await entity_store.save(account)

    # Add all posts to store
    for post_dict in timeline_data["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    # Should not raise even though all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=timeline_data,
        page_type="timeline",
    )


async def test_check_page_duplicates_timeline_new_posts(
    mock_config, entity_store, timeline_data
):
    """Test that check passes when new posts are found."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    account = Account(id=account_id, username="new_posts_test")
    await entity_store.save(account)

    # Add only first post to store — remaining are "new"
    first_post_id = timeline_data["posts"][0]["id"]
    await entity_store.save(Post(id=first_post_id, accountId=account_id))

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=timeline_data,
        page_type="timeline",
        cursor="123",
    )


async def test_check_page_duplicates_timeline_all_existing(
    mock_config, entity_store, timeline_data
):
    """Test detection of all posts already in metadata for timeline."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    account = Account(id=account_id, username="all_existing_test")
    await entity_store.save(account)

    # Add all posts to store
    for post_dict in timeline_data["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", new_callable=AsyncMock),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=timeline_data,
            page_type="timeline",
            cursor="123",
        )

    assert "timeline" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_new_posts(
    mock_config, entity_store, timeline_data
):
    """Test that check passes when new posts are found on wall."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    account = Account(id=account_id, username="wall_new_test")
    await entity_store.save(account)

    # Create wall
    await entity_store.save(Wall(id=wall_id, name="Test Wall", accountId=account_id))

    # Add only first post
    first_post_id = timeline_data["posts"][0]["id"]
    await entity_store.save(Post(id=first_post_id, accountId=account_id))

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=timeline_data,
        page_type="wall",
        page_id=str(wall_id),
        cursor="123",
    )


async def test_check_page_duplicates_wall_all_existing(
    mock_config, entity_store, timeline_data
):
    """Test detection of all posts already in metadata for wall."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    account = Account(id=account_id, username="wall_all_test")
    await entity_store.save(account)

    # Create wall with name
    await entity_store.save(Wall(id=wall_id, name="Test Wall", accountId=account_id))

    # Add all posts to store
    for post_dict in timeline_data["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", new_callable=AsyncMock),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=timeline_data,
            page_type="wall",
            page_id=str(wall_id),
            cursor="123",
        )

    assert "wall" in str(exc_info.value)
    assert "Test Wall" in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_no_name(
    mock_config, entity_store, timeline_data
):
    """Test wall duplicate detection when wall has no name."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    account = Account(id=account_id, username="wall_noname_test")
    await entity_store.save(account)

    # Create wall without name
    await entity_store.save(Wall(id=wall_id, accountId=account_id))

    # Add all posts to store
    for post_dict in timeline_data["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", new_callable=AsyncMock),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=timeline_data,
            page_type="wall",
            page_id=str(wall_id),
            cursor="123",
        )

    assert "wall" in str(exc_info.value)
    assert str(wall_id) in str(exc_info.value)
    assert "123" in str(exc_info.value)


async def test_check_page_duplicates_wall_nonexistent(
    mock_config, entity_store, timeline_data
):
    """Test wall duplicate detection for nonexistent wall."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    account = Account(id=account_id, username="wall_nonexist_test")
    await entity_store.save(account)

    # Add all posts to store, but don't create the wall
    for post_dict in timeline_data["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", new_callable=AsyncMock),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=timeline_data,
            page_type="wall",
            page_id=str(wall_id),
            cursor="123",
        )

    assert "wall" in str(exc_info.value)
    assert str(wall_id) in str(exc_info.value)
    assert "123" in str(exc_info.value)
