"""FactoryBoy factories for SQLAlchemy metadata models.

This module provides factories for creating test instances of SQLAlchemy models
using FactoryBoy. These factories create real database objects with sensible defaults,
replacing the need for extensive Mock usage in tests.

Usage:
    from tests.fixtures import AccountFactory, MediaFactory

    # Create a test account
    account = AccountFactory(username="testuser")

    # Create media with specific values
    media = MediaFactory(accountId=account.id, mimetype="video/mp4")
"""

from datetime import UTC, datetime

import factory
from factory.alchemy import SQLAlchemyModelFactory

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    Group,
    Media,
    MediaLocation,
    Message,
    Post,
)
from metadata.attachment import ContentType


class BaseFactory(SQLAlchemyModelFactory):
    """Base factory for all SQLAlchemy model factories.

    This provides common configuration for all factories, including:
    - Session management
    - Automatic ID generation
    - Timestamp handling
    """

    class Meta:
        """Factory configuration."""

        abstract = True
        # Session will be set via factory_session fixture
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"


class AccountFactory(BaseFactory):
    """Factory for Account model.

    Creates Account instances with realistic defaults.
    Override any fields when creating instances.

    Example:
        account = AccountFactory(username="mycreator", displayName="My Creator")
    """

    class Meta:
        model = Account

    id = factory.Sequence(lambda n: 10000 + n)
    username = factory.Sequence(lambda n: f"user_{n}")
    displayName = factory.LazyAttribute(lambda obj: f"Display {obj.username}")
    flags = 0
    version = 1
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    subscribed = False
    about = factory.LazyAttribute(lambda obj: f"About {obj.username}")
    location = None
    following = False
    profileAccess = True


class MediaFactory(BaseFactory):
    """Factory for Media model.

    Creates Media instances with realistic defaults for images and videos.

    Example:
        # Create an image
        image = MediaFactory(mimetype="image/jpeg", type=1)

        # Create a video
        video = MediaFactory(
            mimetype="video/mp4",
            type=2,
            duration=30.5,
            is_downloaded=True
        )
    """

    class Meta:
        model = Media

    id = factory.Sequence(lambda n: 20000 + n)
    accountId = factory.Sequence(lambda n: 10000 + n)
    meta_info = None
    location = factory.Sequence(lambda n: f"https://example.com/media_{n}.jpg")
    flags = 298
    mimetype = "image/jpeg"
    height = 1080
    width = 1920
    duration = None  # Set for videos
    type = 1  # 1=image, 2=video
    status = 1
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    updatedAt = factory.LazyFunction(lambda: datetime.now(UTC))
    local_filename = None
    content_hash = None
    is_downloaded = False


class MediaLocationFactory(BaseFactory):
    """Factory for MediaLocation model.

    Creates MediaLocation instances linking media to their storage locations.

    Example:
        location = MediaLocationFactory(
            mediaId=media.id,
            locationId=102,
            location="https://cdn.example.com/file.mp4"
        )
    """

    class Meta:
        model = MediaLocation

    mediaId = factory.Sequence(lambda n: 20000 + n)
    locationId = factory.Sequence(lambda n: 100 + n)
    location = factory.Sequence(lambda n: f"https://cdn.example.com/file_{n}.jpg")


class PostFactory(BaseFactory):
    """Factory for Post model.

    Creates Post instances with realistic content and metadata.

    Example:
        post = PostFactory(
            accountId=account.id,
            content="Check out my new post! #awesome"
        )
    """

    class Meta:
        model = Post

    id = factory.Sequence(lambda n: 30000 + n)
    accountId = factory.Sequence(lambda n: 10000 + n)
    content = factory.Sequence(lambda n: f"Test post content {n}")
    fypFlags = 0
    inReplyTo = None
    inReplyToRoot = None
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    expiresAt = None
    likeCount = 0
    replyCount = 0
    mediaLikeCount = 0
    totalTipAmount = 0
    attachmentTipAmount = 0


class GroupFactory(BaseFactory):
    """Factory for Group (conversation) model.

    Creates Group instances for messaging.

    Example:
        group = GroupFactory(createdBy=account.id)
    """

    class Meta:
        model = Group

    id = factory.Sequence(lambda n: 40000 + n)
    createdBy = factory.Sequence(lambda n: 10000 + n)  # Default account ID
    lastMessageId = None
    lastMessageCreatedAt = None
    unreadCount = 0


class MessageFactory(BaseFactory):
    """Factory for Message model.

    Creates Message instances with content and group relationships.

    Example:
        message = MessageFactory(
            groupId=group.id,
            senderId=account.id,
            content="Hello there!"
        )
    """

    class Meta:
        model = Message

    id = factory.Sequence(lambda n: 50000 + n)
    groupId = factory.Sequence(lambda n: 40000 + n)
    senderId = factory.Sequence(lambda n: 10000 + n)
    recipientId = None
    content = factory.Sequence(lambda n: f"Test message content {n}")
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    status = 1


class AttachmentFactory(BaseFactory):
    """Factory for Attachment model.

    Creates Attachment instances linking content to media.

    Example:
        attachment = AttachmentFactory(
            contentId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            accountMediaId=media.id
        )
    """

    class Meta:
        model = Attachment

    id = factory.Sequence(lambda n: 60000 + n)
    contentId = factory.Sequence(lambda n: 30000 + n)
    contentType = ContentType.ACCOUNT_MEDIA  # Use enum, not string
    pos = 0  # Position in attachment list (required NOT NULL field)
    accountMediaId = factory.Sequence(lambda n: 20000 + n)
    bundleId = None


class AccountMediaFactory(BaseFactory):
    """Factory for AccountMedia model.

    Creates AccountMedia instances linking accounts to their media.

    Example:
        account_media = AccountMediaFactory(
            accountId=account.id,
            mediaId=media.id
        )
    """

    class Meta:
        model = AccountMedia

    id = factory.Sequence(lambda n: 70000 + n)
    accountId = factory.Sequence(lambda n: 10000 + n)
    mediaId = factory.Sequence(lambda n: 20000 + n)
    previewId = None
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False
    access = False


class AccountMediaBundleFactory(BaseFactory):
    """Factory for AccountMediaBundle model.

    Creates AccountMediaBundle instances for grouped media.

    Example:
        bundle = AccountMediaBundleFactory(
            accountId=account.id,
            bundleContent='[{"accountMediaId":"123","pos":0}]'
        )
    """

    class Meta:
        model = AccountMediaBundle

    id = factory.Sequence(lambda n: 80000 + n)
    accountId = factory.Sequence(lambda n: 10000 + n)
    previewId = None
    permissionFlags = 0
    price = 0
    createdAt = factory.LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False
    bundleContent = "[]"


# Export all factories
__all__ = [
    "AccountFactory",
    "AccountMediaBundleFactory",
    "AccountMediaFactory",
    "AttachmentFactory",
    "GroupFactory",
    "MediaFactory",
    "MediaLocationFactory",
    "MessageFactory",
    "PostFactory",
]
