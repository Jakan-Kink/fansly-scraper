"""Unit tests for post metadata functionality."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from metadata.attachment import Attachment, ContentType
from metadata.post import Post, pinned_posts, post_mentions, process_pinned_posts


def test_post_model_basic(session, test_account):
    """Test basic Post model functionality."""
    # Create a test post
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post content",
        fypFlag=0,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()

    # Query and verify
    queried_post = session.execute(
        select(Post).where(Post.id == 1)
    ).scalar_one_or_none()
    assert queried_post is not None
    assert queried_post.content == "Test post content"
    assert queried_post.accountId == test_account.id


def test_post_with_attachments(session, test_account):
    """Test Post with attachments relationship."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Post with attachments",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)

    # Add attachments
    attachments = [
        Attachment(
            id=i,
            postId=post.id,
            contentId=f"content_{i}",
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=i,
        )
        for i in range(3)
    ]
    post.attachments.extend(attachments)
    session.commit()

    # Verify attachments
    queried_post = session.execute(
        select(Post).where(Post.id == 1)
    ).scalar_one_or_none()
    assert len(queried_post.attachments) == 3
    assert all(isinstance(a, Attachment) for a in queried_post.attachments)
    assert [a.pos for a in queried_post.attachments] == [0, 1, 2]


def test_post_mentions(session, test_account):
    """Test post mentions relationship."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Post with mentions",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.flush()

    # Add mention
    session.execute(
        post_mentions.insert().values(
            postId=post.id,
            accountId=test_account.id,
            handle="test_handle",
        )
    )
    session.commit()

    # Verify mention
    queried_post = session.execute(
        select(Post).where(Post.id == 1)
    ).scalar_one_or_none()
    assert len(queried_post.accountMentions) == 1
    assert queried_post.accountMentions[0].id == test_account.id


def test_process_pinned_posts(session, test_account, config):
    """Test processing pinned posts."""
    # Create a test post first
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test pinned post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()

    # Test data for pinned posts
    pinned_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]

    # Process pinned posts
    process_pinned_posts(config, test_account, pinned_data, session=session)

    # Verify pinned post
    result = session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == test_account.id,
        )
    ).first()
    assert result is not None
    assert result.pos == 0


def test_process_pinned_posts_nonexistent(session, test_account, config):
    """Test processing pinned posts with nonexistent post."""
    with patch("metadata.post.json_output") as mock_json_output:
        pinned_data = [
            {
                "postId": 999,  # Nonexistent post
                "pos": 0,
                "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]

        process_pinned_posts(config, test_account, pinned_data, session=session)

        # Verify logging
        mock_json_output.assert_any_call(
            1,
            "meta/post - p_p_p - skipping_missing_post",
            {
                "postId": 999,
                "accountId": test_account.id,
                "reason": "Post does not exist in database",
            },
        )


def test_process_pinned_posts_update(session, test_account, config):
    """Test updating existing pinned post."""
    # Create a test post
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test pinned post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()

    # Initial pinned post data
    initial_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    process_pinned_posts(config, test_account, initial_data, session=session)

    # Update with new position
    updated_data = [
        {
            "postId": 1,
            "pos": 1,  # Changed position
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    process_pinned_posts(config, test_account, updated_data, session=session)

    # Verify update
    result = session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == test_account.id,
        )
    ).first()
    assert result is not None
    assert result.pos == 1


def test_post_reply_fields(session, test_account):
    """Test post reply-related fields."""
    # Create parent post
    parent_post = Post(
        id=1,
        accountId=test_account.id,
        content="Parent post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(parent_post)

    # Create reply post
    reply_post = Post(
        id=2,
        accountId=test_account.id,
        content="Reply post",
        inReplyTo=parent_post.id,
        inReplyToRoot=parent_post.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(reply_post)
    session.commit()

    # Verify reply relationships
    queried_reply = session.execute(
        select(Post).where(Post.id == 2)
    ).scalar_one_or_none()
    assert queried_reply.inReplyTo == parent_post.id
    assert queried_reply.inReplyToRoot == parent_post.id


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(timezone.utc),  # With expiration
        None,  # Without expiration
    ],
)
def test_post_expiration(session, test_account, expires_at):
    """Test post expiration field."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
        expiresAt=expires_at,
    )
    session.add(post)
    session.commit()

    queried_post = session.execute(
        select(Post).where(Post.id == 1)
    ).scalar_one_or_none()
    assert queried_post.expiresAt == expires_at


def test_post_cascade_delete(session, test_account):
    """Test cascade deletion of post relationships."""
    # Create post with attachments and mentions
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.flush()

    # Add attachment
    attachment = Attachment(
        id=1,
        postId=post.id,
        contentId="content_1",
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    post.attachments.append(attachment)

    # Add mention
    session.execute(
        post_mentions.insert().values(
            postId=post.id,
            accountId=test_account.id,
            handle="test_handle",
        )
    )
    session.commit()

    # Delete post
    session.delete(post)
    session.commit()

    # Verify cascade deletion
    assert (
        session.execute(select(Post).where(Post.id == 1)).scalar_one_or_none() is None
    )
    assert (
        session.execute(
            select(Attachment).where(Attachment.postId == 1)
        ).scalar_one_or_none()
        is None
    )
    assert (
        session.execute(
            select(post_mentions).where(post_mentions.c.postId == 1)
        ).first()
        is None
    )
