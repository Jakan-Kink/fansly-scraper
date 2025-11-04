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

from factory.alchemy import SQLAlchemyModelFactory
from factory.declarations import LazyAttribute, LazyFunction, Sequence

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    Group,
    Hashtag,
    Media,
    MediaLocation,
    MediaStoryState,
    Message,
    Post,
    Story,
    StubTracker,
    TimelineStats,
    Wall,
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

    id = Sequence(lambda n: 10000 + n)
    username = Sequence(lambda n: f"user_{n}")
    displayName = LazyAttribute(lambda obj: f"Display {obj.username}")
    flags = 0
    version = 1
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    subscribed = False
    about = LazyAttribute(lambda obj: f"About {obj.username}")
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

    id = Sequence(lambda n: 20000 + n)
    accountId = Sequence(lambda n: 10000 + n)
    meta_info = None
    location = Sequence(lambda n: f"https://example.com/media_{n}.jpg")
    flags = 298
    mimetype = "image/jpeg"
    height = 1080
    width = 1920
    duration = None  # Set for videos
    type = 1  # 1=image, 2=video
    status = 1
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    updatedAt = LazyFunction(lambda: datetime.now(UTC))
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

    mediaId = Sequence(lambda n: 20000 + n)
    locationId = Sequence(lambda n: 100 + n)
    location = Sequence(lambda n: f"https://cdn.example.com/file_{n}.jpg")


class PostFactory(BaseFactory):
    """Factory for Post model.

    Creates Post instances with realistic content and metadata.

    Note: Fields like likeCount, replyCount, etc. from the API response are
    intentionally NOT stored in the database and should not be included here.

    Example:
        post = PostFactory(
            accountId=account.id,
            content="Check out my new post! #awesome"
        )
    """

    class Meta:
        model = Post

    id = Sequence(lambda n: 30000 + n)
    accountId = Sequence(lambda n: 10000 + n)
    content = Sequence(lambda n: f"Test post content {n}")
    fypFlag = 0  # Note: singular, not plural (API has fypFlags but we store fypFlag)
    inReplyTo = None
    inReplyToRoot = None
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    expiresAt = None


class GroupFactory(BaseFactory):
    """Factory for Group (conversation) model.

    Creates Group instances for messaging.

    Example:
        group = GroupFactory(createdBy=account.id)
    """

    class Meta:
        model = Group

    id = Sequence(lambda n: 40000 + n)
    createdBy = Sequence(lambda n: 10000 + n)  # Default account ID
    lastMessageId = None


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

    id = Sequence(lambda n: 50000 + n)
    groupId = Sequence(lambda n: 40000 + n)
    senderId = Sequence(lambda n: 10000 + n)
    recipientId = None
    content = Sequence(lambda n: f"Test message content {n}")
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False


class AttachmentFactory(BaseFactory):
    """Factory for Attachment model.

    Creates Attachment instances linking content to media.

    Example:
        # Attachment for a post with ACCOUNT_MEDIA content
        attachment = AttachmentFactory(
            contentId=media.id,  # References AccountMedia.id
            contentType=ContentType.ACCOUNT_MEDIA,
            postId=post.id
        )
        # Attachment for a post with ACCOUNT_MEDIA_BUNDLE content
        attachment = AttachmentFactory(
            contentId=bundle.id,  # References AccountMediaBundle.id
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            postId=post.id
        )
    """

    class Meta:
        model = Attachment

    id = Sequence(lambda n: 60000 + n)
    contentId = Sequence(
        lambda n: 20000 + n
    )  # References AccountMedia.id or AccountMediaBundle.id
    contentType = ContentType.ACCOUNT_MEDIA  # Use enum, not string
    pos = 0  # Position in attachment list (required NOT NULL field)
    postId = None  # Optional: set to link attachment to a post
    messageId = None  # Optional: set to link attachment to a message


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

    id = Sequence(lambda n: 70000 + n)
    accountId = Sequence(lambda n: 10000 + n)
    mediaId = Sequence(lambda n: 20000 + n)
    previewId = None
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False
    access = False


class AccountMediaBundleFactory(BaseFactory):
    """Factory for AccountMediaBundle model.

    Creates AccountMediaBundle instances for grouped media.

    Note: permissionFlags and price are intentionally ignored by the model.
    Note: bundleContent is an API field only - it's not a database column.

    Example:
        bundle = AccountMediaBundleFactory(
            accountId=account.id
        )
    """

    class Meta:
        model = AccountMediaBundle

    id = Sequence(lambda n: 80000 + n)
    accountId = Sequence(lambda n: 10000 + n)
    previewId = None
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False


class HashtagFactory(BaseFactory):
    """Factory for Hashtag model.

    Creates Hashtag instances for post tagging.

    Example:
        hashtag = HashtagFactory(value="test")
    """

    class Meta:
        model = Hashtag

    id = Sequence(lambda n: 90000 + n)
    value = Sequence(lambda n: f"tag_{n}")
    stash_id = None


class StoryFactory(BaseFactory):
    """Factory for Story model.

    Creates Story instances with content and authorship.

    Example:
        story = StoryFactory(
            authorId=account.id,
            title="My Story",
            content="Story content here"
        )
    """

    class Meta:
        model = Story

    id = Sequence(lambda n: 100000 + n)
    authorId = Sequence(lambda n: 10000 + n)
    title = Sequence(lambda n: f"Story Title {n}")
    description = Sequence(lambda n: f"Story description {n}")
    content = Sequence(lambda n: f"Story content {n}")
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    updatedAt = LazyFunction(lambda: datetime.now(UTC))


class WallFactory(BaseFactory):
    """Factory for Wall model.

    Creates Wall instances for organizing posts.

    Example:
        wall = WallFactory(
            accountId=account.id,
            name="My Collection",
            pos=1
        )
    """

    class Meta:
        model = Wall

    id = Sequence(lambda n: 110000 + n)
    accountId = Sequence(lambda n: 10000 + n)
    pos = Sequence(lambda n: n)
    name = Sequence(lambda n: f"Wall {n}")
    description = Sequence(lambda n: f"Wall description {n}")
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    stash_id = None


class MediaStoryStateFactory(BaseFactory):
    """Factory for MediaStoryState model.

    Creates MediaStoryState instances tracking account story state.

    Note: accountId is the primary key for this model.

    Example:
        state = MediaStoryStateFactory(
            accountId=account.id,
            storyCount=5,
            hasActiveStories=True
        )
    """

    class Meta:
        model = MediaStoryState

    accountId = Sequence(lambda n: 10000 + n)
    status = 1
    storyCount = 0
    version = 1
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    updatedAt = LazyFunction(lambda: datetime.now(UTC))
    hasActiveStories = False


class TimelineStatsFactory(BaseFactory):
    """Factory for TimelineStats model.

    Creates TimelineStats instances tracking account content statistics.

    Note: accountId is the primary key for this model.

    Example:
        stats = TimelineStatsFactory(
            accountId=account.id,
            imageCount=50,
            videoCount=25
        )
    """

    class Meta:
        model = TimelineStats

    accountId = Sequence(lambda n: 10000 + n)
    imageCount = 0
    videoCount = 0
    bundleCount = 0
    bundleImageCount = 0
    bundleVideoCount = 0
    fetchedAt = LazyFunction(lambda: datetime.now(UTC))


class StubTrackerFactory(BaseFactory):
    """Factory for StubTracker model.

    Creates StubTracker instances for tracking incomplete records.

    Note: Composite primary key (table_name, record_id).

    Example:
        stub = StubTrackerFactory(
            table_name="accounts",
            record_id=12345,
            reason="message_recipient"
        )
    """

    class Meta:
        model = StubTracker

    table_name = "accounts"
    record_id = Sequence(lambda n: 10000 + n)
    created_at = LazyFunction(lambda: datetime.now(UTC))
    reason = None


async def create_groups_from_messages(session, messages: list[dict]) -> None:
    """Helper to create Group entities for messages that reference groupIds.

    This function should be called AFTER creating all necessary accounts and
    before processing messages to ensure all referenced Groups exist in the
    database, preventing foreign key violations.

    IMPORTANT: This function assumes all referenced accounts already exist.
    Create accounts first before calling this helper.

    Args:
        session: Async database session
        messages: List of message data dictionaries

    Example:
        # First create accounts
        for acc_data in conversation_data["response"]["accounts"]:
            AccountFactory(id=acc_data["id"], username=acc_data["username"])

        # Then create groups
        await create_groups_from_messages(session, conversation_data["response"]["messages"])

        # Finally process messages
        await process_messages_metadata(config, None, messages, session=session)
    """
    from sqlalchemy import select

    groups_to_create = []
    for msg in messages:
        if msg.get("groupId"):
            group_id = int(msg["groupId"])  # Convert to int for bigint column
            # Check if group already created
            result = await session.execute(select(Group).where(Group.id == group_id))
            if not result.scalar_one_or_none():
                # Use senderId or recipientId as createdBy
                created_by_id = int(msg.get("senderId") or msg.get("recipientId"))

                # Create group directly in async session to avoid transaction isolation issues
                group = Group(
                    id=group_id,
                    createdBy=created_by_id,
                    lastMessageId=None,
                )
                session.add(group)
                groups_to_create.append(group_id)

    if groups_to_create:
        await session.commit()
        session.expire_all()


async def setup_accounts_and_groups(
    session, conversation_data: dict, messages: list[dict] | None = None
) -> None:
    """Helper to create accounts and groups from conversation data.

    This is the recommended way to set up test data for message processing tests.
    It handles:
    1. Creating all accounts from conversation_data["response"]["accounts"]
    2. Identifying and creating missing accounts referenced by messages
    3. Creating groups for messages that reference them

    All accounts are created directly in the async session to avoid transaction
    isolation issues between sync and async sessions.

    Args:
        session: Async database session
        conversation_data: Full conversation data with accounts and messages
        messages: Optional specific list of messages (defaults to all messages in conversation_data)

    Example:
        await setup_accounts_and_groups(session, conversation_data)
        await process_messages_metadata(config, None, messages, session=session)
    """

    if messages is None:
        messages = conversation_data.get("response", {}).get("messages", [])

    # Create accounts from explicit accounts list using factory
    account_data = conversation_data.get("response", {}).get("accounts", [])
    account_ids = set()
    for acc_data in account_data:
        acc_id = int(acc_data["id"])
        account_ids.add(acc_id)
        account = AccountFactory.build(
            id=acc_id,
            username=acc_data.get("username", f"user_{acc_id}"),
        )
        session.add(account)
    if account_ids:
        await session.commit()

    # Check what senderIds/recipientIds are in messages
    msg_account_ids = set()
    for msg in messages:
        if msg.get("senderId"):
            msg_account_ids.add(int(msg["senderId"]))
        if msg.get("recipientId"):
            msg_account_ids.add(int(msg["recipientId"]))

    # Create any missing accounts that messages reference using factory
    missing_ids = msg_account_ids - account_ids
    for missing_id in missing_ids:
        account = AccountFactory.build(
            id=missing_id,
            username=f"user_{missing_id}",
        )
        session.add(account)
    if missing_ids:
        await session.commit()

    # Create groups for messages that reference them
    await create_groups_from_messages(session, messages)


# Export all factories
__all__ = [
    "AccountFactory",
    "AccountMediaBundleFactory",
    "AccountMediaFactory",
    "AttachmentFactory",
    "GroupFactory",
    "HashtagFactory",
    "MediaFactory",
    "MediaLocationFactory",
    "MediaStoryStateFactory",
    "MessageFactory",
    "PostFactory",
    "StoryFactory",
    "StubTrackerFactory",
    "TimelineStatsFactory",
    "WallFactory",
    "create_groups_from_messages",
    "setup_accounts_and_groups",
]
