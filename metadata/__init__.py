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
from .account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    TimelineStats,
    account_avatar,
    account_banner,
    account_media_bundle_media,
    process_account_data,
    process_media_bundles,
)
from .attachment import Attachment, ContentType, HasAttachments
from .database import (
    Database,
    get_creator_database_path,
    require_database_config,
    run_migrations_if_needed,
)
from .hashtag import Hashtag, extract_hashtags, post_hashtags, process_post_hashtags
from .media import (
    Media,
    media_variants,
    process_media_download,
    process_media_download_accessible,
    process_media_download_handler,
    process_media_info,
    process_media_metadata,
)
from .messages import Group, Message, process_groups_response, process_messages_metadata
from .post import (
    Post,
    pinned_posts,
    post_mentions,
    process_pinned_posts,
    process_posts_metadata,
    process_timeline_posts,
)
from .story import Story
from .wall import Wall, process_account_walls, process_wall_posts

__all__ = [
    "process_account_data",
    "process_account_walls",
    "process_groups_response",
    "process_media_bundles",
    "process_media_download",
    "process_media_download_accessible",
    "process_media_download_handler",
    "process_media_metadata",
    "process_media_info",
    "process_messages_metadata",
    "process_post_hashtags",
    "process_pinned_posts",
    "process_posts_metadata",
    "process_timeline_posts",
    "process_wall_posts",
    "require_database_config",
    "run_migrations_if_needed",
    "get_creator_database_path",
    "Account",
    "AccountMedia",
    "AccountMediaBundle",
    "Attachment",
    "Base",
    "Database",
    "Media",
    "Message",
    "Post",
    "TimelineStats",
    "Wall",
    "account_avatar",
    "account_banner",
    "account_media_bundle_media",
    "media_variants",
    "pinned_posts",
    "post_mentions",
    "post_hashtags",
    "Hashtag",
    "extract_hashtags",
    "process_post_hashtags",
    "Story",
    "ContentType",
    "HasAttachments",
]
