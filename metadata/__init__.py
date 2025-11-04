"""Metadata database processing and management.

This package provides a comprehensive system for managing and processing metadata
related to social media content. It includes:

Models:
    - Account: User account information and relationships
    - Media: Media items (images, videos) with variants
    - Post: User posts and their attachments
    - Wall: Content organization and display
    - Message: Direct and group messaging

Features:
    - SQLAlchemy ORM models with proper relationships
    - Migration management through Alembic
    - Async and sync database operations
    - Comprehensive logging with rotation
    - Content processing and organization

Usage:
    from metadata import Database, Account, Media

    # Initialize database
    db = Database(config)

    # Process account data
    process_account_data(config, account_data)

    # Handle media content
    process_media_info(config, media_data)
"""

from .base import Base  # isort:skip
from .decorators import with_session  # isort:skip
from .account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    MediaStoryState,
    TimelineStats,
    account_avatar,
    account_banner,
    account_media_bundle_media,
    process_account_data,
    process_media_bundles,
)
from .attachable import Attachable
from .attachment import Attachment, ContentType, HasAttachments
from .database import Database, require_database_config
from .hashtag import Hashtag, extract_hashtags, post_hashtags, process_post_hashtags
from .logging_config import DatabaseLogger
from .media import (
    Media,
    MediaLocation,
    media_variants,
    process_media_download,
    process_media_download_accessible,
    process_media_info,
)
from .media_utils import HasPreview
from .messages import Group, Message, process_groups_response, process_messages_metadata
from .post import (
    Post,
    pinned_posts,
    post_mentions,
    process_pinned_posts,
    process_timeline_posts,
)
from .relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    print_missing_relationships_summary,
)

# Database connection management is now handled internally by Database class
from .story import Story
from .stub_tracker import (
    StubTracker,
    count_stubs,
    get_all_stubs_by_table,
    get_stubs,
    is_stub,
    register_stub,
    remove_stub,
)
from .wall import Wall, process_account_walls, process_wall_posts


__all__ = [
    "Account",
    "AccountMedia",
    "AccountMediaBundle",
    "Attachable",
    "Attachment",
    "Base",
    "ContentType",
    "Database",
    "DatabaseLogger",
    "Group",
    "HasAttachments",
    "HasPreview",
    "Hashtag",
    "Media",
    "MediaLocation",
    "MediaStoryState",
    "Message",
    "Post",
    "Story",
    "StubTracker",
    "TimelineStats",
    "Wall",
    "account_avatar",
    "account_banner",
    "account_media_bundle_media",
    "clear_missing_relationships",
    "count_stubs",
    "extract_hashtags",
    "get_all_stubs_by_table",
    "get_stubs",
    "is_stub",
    "log_missing_relationship",
    "media_variants",
    "pinned_posts",
    "post_hashtags",
    "post_mentions",
    "print_missing_relationships_summary",
    "process_account_data",
    "process_account_walls",
    "process_groups_response",
    "process_media_bundles",
    "process_media_download",
    "process_media_download_accessible",
    "process_media_info",
    "process_messages_metadata",
    "process_pinned_posts",
    "process_post_hashtags",
    "process_post_hashtags",
    "process_timeline_posts",
    "process_wall_posts",
    "register_stub",
    "remove_stub",
    "require_database_config",
    "with_session",
]
