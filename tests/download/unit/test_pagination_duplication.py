"""Test pagination duplication detection functionality."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from download.common import check_page_duplicates
from errors import DuplicatePageError
from metadata.models import Account, Message, Post, Wall
from tests.fixtures.utils import scaled_async_sleep
from tests.fixtures.utils.test_isolation import snowflake_id


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


async def test_check_page_duplicates_disabled(
    mock_config, entity_store, synthetic_timeline_page
):
    """Test that check is skipped when feature is disabled."""
    mock_config.use_pagination_duplication = False

    account_id = snowflake_id()
    account = Account(id=account_id, username="disabled_test")
    await entity_store.save(account)

    # Add all posts to store
    for post_dict in synthetic_timeline_page["posts"]:
        await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    # Should not raise even though all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=synthetic_timeline_page,
        page_type="timeline",
    )


async def test_check_page_duplicates_timeline_new_posts(
    mock_config, entity_store, synthetic_timeline_page
):
    """Test that check passes when new posts are found."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    account = Account(id=account_id, username="new_posts_test")
    await entity_store.save(account)

    # Add only first post to store — remaining are "new"
    first_post_id = synthetic_timeline_page["posts"][0]["id"]
    await entity_store.save(Post(id=first_post_id, accountId=account_id))

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=synthetic_timeline_page,
        page_type="timeline",
        cursor="123",
    )


@pytest.mark.parametrize(
    ("kind", "page_type", "wall_name", "use_wall", "cursor"),
    [
        ("timeline", "timeline", None, False, "123"),
        ("wall_named", "wall", "Test Wall", True, "123"),
        ("wall_noname", "wall", None, True, "123"),
        ("wall_nonexistent", "wall", None, False, "123"),
        ("messages", "messages", None, False, "500"),
    ],
    ids=[
        "timeline_all_existing",
        "wall_all_existing_named",
        "wall_no_name",
        "wall_nonexistent",
        "messages_all_existing",
    ],
)
async def test_check_page_duplicates_all_existing_raises(
    kind,
    page_type,
    wall_name,
    use_wall,
    cursor,
    mock_config,
    entity_store,
    synthetic_timeline_page,
    messages_page_data,
):
    """All items on a page already cached → DuplicatePageError across every
    page_type, with the page-type-specific noun and identifier in the message.

    Runs the real ``check_page_duplicates`` pipeline + entity_store; the only
    boundary is the scaled-sleep accelerator standing in for the 5s pre-raise
    pause (real await, ~0 duration).
    """
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    page_id: str | None
    await entity_store.save(Account(id=account_id, username=f"{kind}_test"))

    if page_type == "messages":
        page_data = messages_page_data
        page_id = str(snowflake_id())
        sender_id = messages_page_data["messages"][0]["senderId"]
        await entity_store.save(Account(id=sender_id, username="msg_sender"))
        for msg in messages_page_data["messages"]:
            await entity_store.save(
                Message(
                    id=msg["id"],
                    senderId=sender_id,
                    content="x",
                    createdAt=datetime.now(UTC),
                )
            )
    else:
        page_data = synthetic_timeline_page
        page_id = str(wall_id) if page_type == "wall" else None
        if use_wall:
            await entity_store.save(
                Wall(id=wall_id, name=wall_name, accountId=account_id)
            )
        for post_dict in synthetic_timeline_page["posts"]:
            await entity_store.save(Post(id=post_dict["id"], accountId=account_id))

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", scaled_async_sleep),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=page_data,
            page_type=page_type,
            page_id=page_id,
            cursor=cursor,
            bypass=False,
        )

    err = str(exc_info.value)
    assert page_type in err
    assert cursor in err
    if kind == "wall_named":
        assert "Test Wall" in err
    elif kind in ("wall_noname", "wall_nonexistent"):
        assert str(wall_id) in err
    elif kind == "messages":
        assert "All messages on messages" in err


async def test_check_page_duplicates_wall_new_posts(
    mock_config, entity_store, synthetic_timeline_page
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
    first_post_id = synthetic_timeline_page["posts"][0]["id"]
    await entity_store.save(Post(id=first_post_id, accountId=account_id))

    # Should not raise since not all posts are in metadata
    await check_page_duplicates(
        config=mock_config,
        page_data=synthetic_timeline_page,
        page_type="wall",
        page_id=str(wall_id),
        cursor="123",
    )


async def test_check_page_duplicates_messages_new_messages_no_raise(
    mock_config, entity_store, messages_page_data
):
    """Mix of cached + new messages → returns silently (we still have new
    content to process)."""
    mock_config.use_pagination_duplication = True

    sender_id = messages_page_data["messages"][0]["senderId"]
    await entity_store.save(Account(id=sender_id, username="msg_partial"))
    first_msg = messages_page_data["messages"][0]
    await entity_store.save(
        Message(
            id=first_msg["id"],
            senderId=sender_id,
            content="cached",
            createdAt=datetime.now(UTC),
        )
    )

    await check_page_duplicates(
        config=mock_config,
        page_data=messages_page_data,
        page_type="messages",
        cursor="500",
    )


async def test_check_page_duplicates_bypass_skips_all_page_types(
    mock_config, entity_store, timeline_data, messages_page_data
):
    """bypass=True suppresses the raise across every supported page_type
    even when every item is cached AND use_pagination_duplication is on —
    the access-changed contract relies on this short-circuit."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    wall_id = snowflake_id()
    sender_id = messages_page_data["messages"][0]["senderId"]
    await entity_store.save(Account(id=account_id, username="bypass_test"))
    await entity_store.save(Account(id=sender_id, username="bypass_sender"))
    await entity_store.save(Wall(id=wall_id, name="W", accountId=account_id))
    for post in timeline_data["response"]["posts"]:
        await entity_store.save(Post(id=post["id"], accountId=account_id))
    for msg in messages_page_data["messages"]:
        await entity_store.save(
            Message(
                id=msg["id"],
                senderId=sender_id,
                content="x",
                createdAt=datetime.now(UTC),
            )
        )

    for page_type, page_data, page_id in (
        ("timeline", timeline_data, account_id),
        ("wall", timeline_data, wall_id),
        ("messages", messages_page_data, sender_id),
    ):
        await check_page_duplicates(
            config=mock_config,
            page_data=page_data,
            page_type=page_type,
            page_id=str(page_id),
            cursor="100",
            bypass=True,
        )
