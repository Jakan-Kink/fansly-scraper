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

import os
import random
import time
from datetime import UTC, datetime

from factory.alchemy import SQLAlchemyModelFactory
from factory.declarations import LazyAttribute, LazyFunction, Sequence
from faker import Faker
from faker.providers import BaseProvider

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


# Helper for pytest-xdist worker isolation
def _get_worker_offset():
    """Generate unique ID offset per pytest-xdist worker.

    Combines monotonic_ns() with worker ID to ensure each parallel worker
    gets a non-overlapping ID range, preventing race conditions in shared resources.

    Returns:
        int: Unique offset for this worker's ID range
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")

    # Convert worker ID to numeric offset
    worker_num = 0 if worker_id == "master" else int(worker_id.replace("gw", "")) + 1

    # Combine monotonic_ns + worker for true uniqueness
    # 20 bits for time (~1M range per worker), 12 bits for worker (4096 workers max)
    time_offset = int(time.monotonic_ns()) % (2**20)
    worker_offset = worker_num * (2**20)

    return time_offset + worker_offset


# 60-bit ID ranges for BigInteger testing
# Each entity type gets a 100 trillion range within the 60-bit space (2^60 - 1)
# This simulates realistic Fansly API IDs which are large snowflake-like integers
#
# Uses monotonic_ns() + worker_id for parallel test isolation - each pytest-xdist
# worker gets a unique, non-overlapping ID range, preventing collisions
_ID_OFFSET = _get_worker_offset()

ACCOUNT_ID_BASE = 100_000_000_000_000_000 + _ID_OFFSET  # Accounts
MEDIA_ID_BASE = 200_000_000_000_000_000 + _ID_OFFSET  # Media
MEDIA_LOCATION_ID_BASE = 210_000_000_000_000_000 + _ID_OFFSET  # MediaLocations
POST_ID_BASE = 300_000_000_000_000_000 + _ID_OFFSET  # Posts
GROUP_ID_BASE = 400_000_000_000_000_000 + _ID_OFFSET  # Groups (conversations)
MESSAGE_ID_BASE = 500_000_000_000_000_000 + _ID_OFFSET  # Messages
ATTACHMENT_ID_BASE = 600_000_000_000_000_000 + _ID_OFFSET  # Attachments
ACCOUNT_MEDIA_ID_BASE = 700_000_000_000_000_000 + _ID_OFFSET  # AccountMedia
ACCOUNT_MEDIA_BUNDLE_ID_BASE = (
    800_000_000_000_000_000 + _ID_OFFSET
)  # AccountMediaBundle
HASHTAG_ID_BASE = 900_000_000_000_000_000 + _ID_OFFSET  # Hashtags
STORY_ID_BASE = 1_000_000_000_000_000_000 + _ID_OFFSET  # Stories (within 60-bit max)
WALL_ID_BASE = 110_000_000_000_000_000 + _ID_OFFSET  # Walls


# Custom Faker provider for realistic Fansly content
class FanslyContentProvider(BaseProvider):
    """Faker provider for generating realistic Fansly post/message content.

    Based on analysis of real Fansly API responses, content follows these patterns:
    1. Short teaser posts (27-60 chars): Questions with emojis
    2. Medium posts (80-200 chars): Description + hashtag block
    3. Long narrative posts (300-600 chars): Story-style content with hashtags
    """

    # Realistic short teaser templates (< 60 chars)
    SHORT_TEASERS = [
        "Would you like a squeeze? 🐮",
        "What naughty things would you whisper to me? 😳",
        "Did you have nice dreams about me? 🤭😏",
        "Do you like the view? 🔥",
        "A little sneak peak at some photos I created today ;)",
        "Ready for something special? 💋",
        "Miss me? 😘",
        "Should I post the full video? 👀",
        "Good morning, my dears! ☀️💕",
        "Happy Friday! Let's celebrate 🎉",
        "New content dropping soon... 👀🔥",
        "Who wants to see more? 💜",
        "This outfit was so fun to wear! 😈",
        "Behind the scenes from today's shoot 📸",
        "Just uploaded something special for you 💝",
    ]

    # Medium post templates with hashtag placeholders
    MEDIUM_TEMPLATES = [
        "Do you like my outfit? 😈\n\n{hashtags}",
        "Living the dream! 😎💜\n\n{hashtags}",
        "Will it go in….\n\n{hashtags}",
        "Do you immediately shove it in me, or will you give me a good spanking first? 😈\n\n{hashtags}",
        "Slide my panties to the side and pleasure me 🥵🥵\n\n{hashtags}",
        "And here are a few impressions from yesterday's session 🥺\n\n{hashtags}",
        "The sales just started! 🖤🛒 Use my code for an additional discount!\n\n{hashtags}",
        "Want to watch the full session? 😍 Make sure you have your renew switched on!\n\n{hashtags}",
    ]

    # Long narrative post templates
    LONG_TEMPLATES = [
        "Daily Fit\n\nA good day starts with the right mindset. And today, my routine comes with an added challenge. There's something about the preparation—both physical and mental—that gets me into the right headspace before anything else. It's not just about being ready, it's about being present.\n\n{hashtags}",
        "Story Time\n\nShooting a big custom video today, and that always comes with its own little rituals. There's something about the preparation—both physical and mental—that gets me into the right headspace before a scene. It's not just about being ready for the camera, it's about being ready.\n\n{hashtags}",
        "Happy Monday, my dears! 🖤\n\nWhether you're starting the week strong or just trying to survive it, I hope you find something (or someone) to keep you motivated. 😉🔥 Let's make it a good one! Drop a comment and let me know how your week is going.\n\n{hashtags}",
        "This week on my page, things are getting INTENSE 🥵\n\nHere's what we got in stock for you:\n🔥 A strict session\n🖤 A bedtime ritual\n❄️ Story Post: Something special\n\nStay tuned for more!\n\n{hashtags}",
    ]

    # Hashtag categories (realistic Fansly hashtags)
    HASHTAG_CATEGORIES = {
        "body": [
            "#bigass",
            "#boobs",
            "#tits",
            "#curves",
            "#petite",
            "#skinny",
            "#blonde",
            "#redhead",
            "#brunette",
        ],
        "fetish": [
            "#latex",
            "#rubber",
            "#bdsm",
            "#fetish",
            "#kink",
            "#kinky",
            "#submissive",
            "#dominant",
        ],
        "activity": [
            "#gym",
            "#workout",
            "#selfie",
            "#photoshoot",
            "#behindthescenes",
            "#newcontent",
        ],
        "clothing": [
            "#leggings",
            "#catsuit",
            "#lingerie",
            "#heels",
            "#outfit",
            "#ootd",
        ],
        "mood": ["#sexy", "#hot", "#naughty", "#playful", "#teasing", "#flirty"],
        "general": [
            "#fyp",
            "#fansly",
            "#exclusive",
            "#subscribe",
            "#newpost",
            "#contentcreator",
        ],
    }

    def fansly_post_content(self, length: str = "random") -> str:
        """Generate realistic Fansly post content.

        Args:
            length: "short", "medium", "long", or "random"

        Returns:
            Realistic post content string
        """
        if length == "random":
            length = random.choice(["short", "short", "medium", "medium", "long"])  # noqa: S311

        if length == "short":
            return random.choice(self.SHORT_TEASERS)  # noqa: S311
        if length == "medium":
            template = random.choice(self.MEDIUM_TEMPLATES)  # noqa: S311
            hashtags = self._generate_hashtags(5, 10)
            return template.format(hashtags=hashtags)
        # long
        template = random.choice(self.LONG_TEMPLATES)  # noqa: S311
        hashtags = self._generate_hashtags(8, 15)
        return template.format(hashtags=hashtags)

    def fansly_message_content(self) -> str:
        """Generate realistic Fansly message content (typically shorter)."""
        # Messages are usually shorter and more personal
        templates = [
            "Hey! Thanks for subscribing 💕",
            "Check out my latest post! 🔥",
            "I just uploaded something special for you 😘",
            "Miss you! Come say hi 👋",
            "New video just dropped! 🎬",
            "Thanks for the tip! 💝 Here's a little something extra...",
            "Happy to have you here! Let me know what content you'd like to see 😊",
        ]
        return random.choice(templates)  # noqa: S311

    def fansly_hashtags(self, count: int = 5) -> str:
        """Generate realistic Fansly hashtags."""
        return self._generate_hashtags(count, count)

    def _generate_hashtags(self, min_count: int, max_count: int) -> str:
        """Generate a string of hashtags from various categories."""
        count = random.randint(min_count, max_count)  # noqa: S311
        all_tags = []
        for tags in self.HASHTAG_CATEGORIES.values():
            all_tags.extend(tags)
        selected = random.sample(all_tags, min(count, len(all_tags)))
        return " ".join(selected)


# Create a Faker instance with our custom provider
fake = Faker()
fake.add_provider(FanslyContentProvider)


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
        account_with_stash = AccountFactory(username="creator", stash_id=12345)
    """

    class Meta:
        model = Account

    id = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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
    stash_id = None  # Optional: integer ID in Stash for this account


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

    id = Sequence(lambda n: MEDIA_ID_BASE + n)
    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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

    mediaId = Sequence(lambda n: MEDIA_ID_BASE + n)
    locationId = Sequence(lambda n: MEDIA_LOCATION_ID_BASE + n)
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

    id = Sequence(lambda n: POST_ID_BASE + n)
    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
    # Use short teasers only (no hashtags) for Stash integration tests.
    # Full hashtag-linked content requires going through process_post_hashtags()
    # which is reserved for true end-to-end Fansly→Stash flow tests.
    content = LazyFunction(lambda: fake.fansly_post_content(length="short"))
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

    id = Sequence(lambda n: GROUP_ID_BASE + n)
    createdBy = Sequence(lambda n: ACCOUNT_ID_BASE + n)  # Default account ID
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

    id = Sequence(lambda n: MESSAGE_ID_BASE + n)
    groupId = Sequence(lambda n: GROUP_ID_BASE + n)
    senderId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
    recipientId = None
    # Use realistic Fansly message content by default
    content = LazyFunction(lambda: fake.fansly_message_content())
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

    id = Sequence(lambda n: ATTACHMENT_ID_BASE + n)
    contentId = Sequence(
        lambda n: ACCOUNT_MEDIA_ID_BASE + n
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

    id = Sequence(lambda n: ACCOUNT_MEDIA_ID_BASE + n)
    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
    mediaId = Sequence(lambda n: MEDIA_ID_BASE + n)
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

    id = Sequence(lambda n: ACCOUNT_MEDIA_BUNDLE_ID_BASE + n)
    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
    previewId = None
    createdAt = LazyFunction(lambda: datetime.now(UTC))
    deletedAt = None
    deleted = False


class HashtagFactory(BaseFactory):
    """Factory for Hashtag model.

    Creates Hashtag instances for post tagging.

    Example:
        hashtag = HashtagFactory(value="test")

    Note:
        ID is NOT set by factory - Hashtag uses autoincrement Integer (32-bit).
        Hashtags are extracted from post content using # symbols, not from Fansly API.
    """

    class Meta:
        model = Hashtag

    # ID is autoincrement - do not set it
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

    id = Sequence(lambda n: STORY_ID_BASE + n)
    authorId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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

    id = Sequence(lambda n: WALL_ID_BASE + n)
    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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

    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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

    accountId = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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
    record_id = Sequence(lambda n: ACCOUNT_ID_BASE + n)
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


# Export all factories and utilities
__all__ = [
    "AccountFactory",
    "AccountMediaBundleFactory",
    "AccountMediaFactory",
    "AttachmentFactory",
    "FanslyContentProvider",
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
    "fake",
    "setup_accounts_and_groups",
]
