"""Metadata database processing and management.

Architecture:
- Pydantic models (models.py) for data + validation + identity map
- PostgresEntityStore (entity_store.py) for persistence + caching
- SA Core tables (tables.py) for Alembic migrations only
"""

# Processing functions
from .account import process_account_data, process_media_bundles
from .attachment import HasAttachments
from .database import Database
from .entity_store import OrderBySpec, PostgresEntityStore, SortDirection
from .hashtag import extract_hashtags, process_post_hashtags
from .logging_config import DatabaseLogger, get_db_logger
from .media import process_media_download, process_media_info
from .media_utils import HasPreview
from .messages import process_groups_response, process_messages_metadata
from .models import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    ContentType,
    FanslyObject,
    FanslyRecord,
    FollowEvent,
    Group,
    Hashtag,
    Media,
    MediaLocation,
    MediaStory,
    MediaStoryState,
    Message,
    MonitorState,
    PinnedPost,
    Post,
    PostMention,
    SnowflakeId,
    StubTracker,
    Subscription,
    SubscriptionPlan,
    SubscriptionPromo,
    TimelineStats,
    Wall,
    get_store,
)
from .post import process_pinned_posts, process_timeline_posts
from .relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    print_missing_relationships_summary,
)
from .story import process_media_stories
from .stub_tracker import (
    count_stubs,
    get_all_stubs_by_table,
    get_stubs,
    is_stub,
    register_stub,
    remove_stub,
)
from .subscriptions import (
    apply_subscription_snapshot,
    apply_subscription_ws_event,
    process_subscriptions_response,
    record_follow_observation,
)
from .wall import process_account_walls, process_wall_posts


__all__ = [
    "Account",
    "AccountMedia",
    "AccountMediaBundle",
    "Attachment",
    "ContentType",
    "Database",
    "DatabaseLogger",
    "FanslyObject",
    "FanslyRecord",
    "FollowEvent",
    "Group",
    "HasAttachments",
    "HasPreview",
    "Hashtag",
    "Media",
    "MediaLocation",
    "MediaStory",
    "MediaStoryState",
    "Message",
    "MonitorState",
    "OrderBySpec",
    "PinnedPost",
    "Post",
    "PostMention",
    "PostgresEntityStore",
    "SnowflakeId",
    "SortDirection",
    "StubTracker",
    "Subscription",
    "SubscriptionPlan",
    "SubscriptionPromo",
    "TimelineStats",
    "Wall",
    "apply_subscription_snapshot",
    "apply_subscription_ws_event",
    "clear_missing_relationships",
    "count_stubs",
    "extract_hashtags",
    "get_all_stubs_by_table",
    "get_db_logger",
    "get_store",
    "get_stubs",
    "is_stub",
    "log_missing_relationship",
    "print_missing_relationships_summary",
    "process_account_data",
    "process_account_walls",
    "process_groups_response",
    "process_media_bundles",
    "process_media_download",
    "process_media_info",
    "process_media_stories",
    "process_messages_metadata",
    "process_pinned_posts",
    "process_post_hashtags",
    "process_subscriptions_response",
    "process_timeline_posts",
    "process_wall_posts",
    "record_follow_observation",
    "register_stub",
    "remove_stub",
]
