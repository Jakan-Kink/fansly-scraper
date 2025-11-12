"""Metadata fixtures for testing metadata models and processing."""

from .metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    BaseFactory,
    GroupFactory,
    HashtagFactory,
    MediaFactory,
    MediaLocationFactory,
    MediaStoryStateFactory,
    MessageFactory,
    PostFactory,
    StoryFactory,
    StubTrackerFactory,
    TimelineStatsFactory,
    WallFactory,
    create_groups_from_messages,
    setup_accounts_and_groups,
)
from .metadata_fixtures import (
    test_account,
    test_account_media,
    test_attachment,
    test_group,
    test_media,
    test_media_bundle,
    test_message,
    test_messages,
    test_post,
    test_posts,
)

# Alias GroupFactory to avoid collision with Stash GroupFactory
MetadataGroupFactory = GroupFactory

__all__ = [
    # Factories
    "AccountFactory",
    "AccountMediaBundleFactory",
    "AccountMediaFactory",
    "AttachmentFactory",
    "BaseFactory",
    "GroupFactory",
    "MetadataGroupFactory",
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
    # Fixtures
    "test_account",
    "test_account_media",
    "test_attachment",
    "test_group",
    "test_media",
    "test_media_bundle",
    "test_message",
    "test_messages",
    "test_post",
    "test_posts",
]
