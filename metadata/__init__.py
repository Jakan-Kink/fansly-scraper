"""Metadata database processing and management.

Architecture:
- Pydantic models (models.py) for data + validation + identity map
- PostgresEntityStore (entity_store.py) for persistence + caching
- SA Core tables (tables.py) for Alembic migrations only
"""

# Processing functions
from .account import (
    process_account_data,
    process_media_bundles,
)
from .attachment import HasAttachments
from .database import Database
from .entity_store import OrderBySpec, PostgresEntityStore, SortDirection
from .hashtag import extract_hashtags, process_post_hashtags
from .logging_config import DatabaseLogger, get_db_logger
from .media import (
    process_media_download,
    process_media_info,
)
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
    Group,
    Hashtag,
    Media,
    MediaLocation,
    MediaStoryState,
    Message,
    PinnedPost,
    Post,
    PostMention,
    SnowflakeId,
    Story,
    StubTracker,
    TimelineStats,
    Wall,
)
from .post import (
    process_pinned_posts,
    process_timeline_posts,
)
from .relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    print_missing_relationships_summary,
)
from .stub_tracker import (
    count_stubs,
    get_all_stubs_by_table,
    get_stubs,
    is_stub,
    register_stub,
    remove_stub,
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
    "Group",
    "HasAttachments",
    "HasPreview",
    "Hashtag",
    "Media",
    "MediaLocation",
    "MediaStoryState",
    "Message",
    "OrderBySpec",
    "PinnedPost",
    "Post",
    "PostMention",
    "PostgresEntityStore",
    "SnowflakeId",
    "SortDirection",
    "Story",
    "StubTracker",
    "TimelineStats",
    "Wall",
    "clear_missing_relationships",
    "count_stubs",
    "extract_hashtags",
    "get_all_stubs_by_table",
    "get_db_logger",
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
    "process_messages_metadata",
    "process_pinned_posts",
    "process_post_hashtags",
    "process_timeline_posts",
    "process_wall_posts",
    "register_stub",
    "remove_stub",
]
