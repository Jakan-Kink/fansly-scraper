"""SA Core table definitions for Alembic migrations.

These Table() definitions produce the EXACT same schema as the current SA ORM
models. They are used ONLY by Alembic for migration generation and schema diffing.

Runtime code uses Pydantic models + asyncpg pool (entity_store.py).

IMPORTANT: Any schema change must be made here AND reflected in the Pydantic models.
Run ``alembic check`` after any change to verify zero drift.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SQLEnum


metadata = MetaData()

# ── ContentType Enum ────────────────────────────────────────────────────
# Matches metadata.attachment.ContentType Python enum.
# PostgreSQL type name "contenttype" matches what SQLEnum(ContentType) generates.

_content_type_enum = SQLEnum(
    "ACCOUNT_MEDIA",
    "ACCOUNT_MEDIA_BUNDLE",
    "AGGREGATED_POSTS",
    "TIP_GOALS",
    "STORY",
    "POLL",
    name="contenttype",
)

# ── Entity Tables ───────────────────────────────────────────────────────

accounts = Table(
    "accounts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("username", String, unique=True, nullable=False),
    Column("displayName", String, nullable=True),
    Column("flags", Integer, nullable=True),
    Column("version", Integer, nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=True),
    Column("subscribed", Boolean, nullable=True),
    Column("about", String, nullable=True),
    Column("location", String, nullable=True),
    Column("following", Boolean, nullable=True),
    Column("profileAccess", Boolean, nullable=True),
    Column("stash_id", Integer, nullable=True),
)

media = Table(
    "media",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("accountId", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("meta_info", String, nullable=True),
    Column("location", String, nullable=True),
    Column("flags", Integer, nullable=True),
    Column("mimetype", String, nullable=True),
    Column("height", Integer, nullable=True),
    Column("width", Integer, nullable=True),
    Column("duration", Float, nullable=True),
    Column("type", Integer, nullable=True),
    Column("status", Integer, nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=True),
    Column("updatedAt", DateTime(timezone=True), nullable=True),
    Column("local_filename", String, nullable=True),
    Column("content_hash", String, nullable=True),
    Column(
        "is_downloaded",
        Boolean,
        nullable=False,
        server_default="false",
    ),
    Column("stash_id", Integer, nullable=True),
    Index("ix_media_content_hash", "content_hash"),
)

media_locations = Table(
    "media_locations",
    metadata,
    Column("mediaId", BigInteger, ForeignKey("media.id"), primary_key=True),
    Column("locationId", BigInteger, primary_key=True),
    Column("location", String, nullable=False),
    # Explicit UC exists in the database (created by migration), redundant with PK
    # but kept here to match the actual schema for zero Alembic drift.
    UniqueConstraint("mediaId", "locationId"),
)


posts = Table(
    "posts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("accountId", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("content", String, nullable=True),
    Column("fypFlag", Integer, nullable=True),
    Column("inReplyTo", BigInteger, nullable=True),
    Column("inReplyToRoot", BigInteger, nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=True),
    Column("expiresAt", DateTime(timezone=True), nullable=True),
    Column("stash_id", Integer, nullable=True),
)

messages = Table(
    "messages",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("groupId", BigInteger, ForeignKey("groups.id"), nullable=True, index=True),
    Column("senderId", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("recipientId", BigInteger, ForeignKey("accounts.id"), nullable=True),
    Column("content", String, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=False),
    Column("deletedAt", DateTime(timezone=True), nullable=True),
    Column("deleted", Boolean, nullable=False),
    Column("stash_id", Integer, nullable=True),
)

groups = Table(
    "groups",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("createdBy", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("lastMessageId", BigInteger, nullable=True),
)

walls = Table(
    "walls",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "accountId",
        BigInteger,
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
    ),
    Column("pos", Integer, nullable=True),
    Column("name", String, nullable=True),
    Column("description", String, nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=True),
    Column("stash_id", Integer, nullable=True),
    Index("idx_wall_account_created", "accountId", "createdAt"),
)

attachments = Table(
    "attachments",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("postId", BigInteger, ForeignKey("posts.id"), nullable=True),
    Column("messageId", BigInteger, ForeignKey("messages.id"), nullable=True),
    Column("contentId", BigInteger, nullable=False),
    Column("pos", Integer, nullable=False),
    Column("contentType", _content_type_enum, nullable=False),
    CheckConstraint(
        '"postId" IS NULL OR "messageId" IS NULL',
        name="check_post_or_message_exclusivity",
    ),
)

stories = Table(
    "stories",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("authorId", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("title", String, nullable=True),
    Column("description", String, nullable=True),
    Column("content", String, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=False),
    Column("updatedAt", DateTime(timezone=True), nullable=True),
)

hashtags = Table(
    "hashtags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("value", String, nullable=False),
    Column("stash_id", Integer, nullable=True),
)

# Functional index must be defined after the table so we can reference the column
Index("ix_hashtags_value_lower", func.lower(hashtags.c.value), unique=True)

timeline_stats = Table(
    "timeline_stats",
    metadata,
    Column("accountId", BigInteger, ForeignKey("accounts.id"), primary_key=True),
    Column("imageCount", Integer, nullable=True),
    Column("videoCount", Integer, nullable=True),
    Column("bundleCount", Integer, nullable=True),
    Column("bundleImageCount", Integer, nullable=True),
    Column("bundleVideoCount", Integer, nullable=True),
    Column("fetchedAt", DateTime(timezone=True), nullable=True),
)

media_story_states = Table(
    "media_story_states",
    metadata,
    Column("accountId", BigInteger, ForeignKey("accounts.id"), primary_key=True),
    Column("status", Integer, nullable=True),
    Column("storyCount", Integer, nullable=True),
    Column("version", Integer, nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=True),
    Column("updatedAt", DateTime(timezone=True), nullable=True),
    Column("hasActiveStories", Boolean, nullable=True),
)

account_media = Table(
    "account_media",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "accountId",
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "mediaId",
        BigInteger,
        ForeignKey("media.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("previewId", BigInteger, ForeignKey("media.id"), nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=False),
    Column("deletedAt", DateTime(timezone=True), nullable=True),
    Column("deleted", Boolean, nullable=False),
    Column("access", Boolean, nullable=False),
    Column("stash_id", Integer, nullable=True),
)

account_media_bundles = Table(
    "account_media_bundles",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("accountId", BigInteger, ForeignKey("accounts.id"), nullable=False),
    Column("previewId", BigInteger, ForeignKey("media.id"), nullable=True),
    Column("createdAt", DateTime(timezone=True), nullable=False),
    Column("deletedAt", DateTime(timezone=True), nullable=True),
    Column("deleted", Boolean, nullable=False),
    Column("access", Boolean, nullable=False),
    Column("purchased", Boolean, nullable=False),
    Column("whitelisted", Boolean, nullable=False),
    Column("stash_id", Integer, nullable=True),
)

stub_tracker = Table(
    "stub_tracker",
    metadata,
    Column("table_name", String, primary_key=True, index=True),
    Column("record_id", BigInteger, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("reason", String, nullable=True),
    UniqueConstraint("table_name", "record_id", name="uix_stub_tracker"),
)

# ── Junction / Association Tables ───────────────────────────────────────

account_avatar = Table(
    "account_avatar",
    metadata,
    Column("accountId", BigInteger, ForeignKey("accounts.id")),
    Column("mediaId", BigInteger, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)

account_banner = Table(
    "account_banner",
    metadata,
    Column("accountId", BigInteger, ForeignKey("accounts.id")),
    Column("mediaId", BigInteger, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)

account_media_bundle_media = Table(
    "account_media_bundle_media",
    metadata,
    Column(
        "bundle_id",
        BigInteger,
        ForeignKey("account_media_bundles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "media_id",
        BigInteger,
        ForeignKey("account_media.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("pos", Integer, nullable=False),
)

media_variants = Table(
    "media_variants",
    metadata,
    Column("mediaId", BigInteger, ForeignKey("media.id"), primary_key=True),
    Column("variantId", BigInteger, ForeignKey("media.id"), primary_key=True),
    UniqueConstraint("mediaId", "variantId"),
)

pinned_posts = Table(
    "pinned_posts",
    metadata,
    Column("accountId", BigInteger, ForeignKey("accounts.id"), primary_key=True),
    Column("postId", BigInteger, ForeignKey("posts.id"), primary_key=True),
    Column("pos", Integer, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=True),
)

post_mentions = Table(
    "post_mentions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "postId",
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("accountId", BigInteger, nullable=True),
    Column("handle", String, nullable=False),
    UniqueConstraint("postId", "accountId", name="uix_post_mentions_account"),
    UniqueConstraint("postId", "handle", name="uix_post_mentions_handle"),
    Index("ix_post_mentions_accountId", "accountId"),
    Index(
        "ix_post_mentions_account",
        "postId",
        "accountId",
        postgresql_where=text('"accountId" IS NOT NULL'),
    ),
    Index(
        "ix_post_mentions_handle",
        "postId",
        "handle",
        postgresql_where=text('"handle" IS NOT NULL'),
    ),
)

post_hashtags = Table(
    "post_hashtags",
    metadata,
    Column(
        "postId",
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "hashtagId",
        Integer,
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    UniqueConstraint("postId", "hashtagId", name="pk_post_hashtags"),
    Index("ix_post_hashtags_postId", "postId"),
    Index("ix_post_hashtags_hashtagId", "hashtagId"),
)

wall_posts = Table(
    "wall_posts",
    metadata,
    Column("wallId", BigInteger, ForeignKey("walls.id"), primary_key=True),
    Column("postId", BigInteger, ForeignKey("posts.id"), primary_key=True),
    Index("idx_wall_posts_post", "postId"),
    Index("idx_wall_posts_wall_post", "wallId", "postId"),
)

group_users = Table(
    "group_users",
    metadata,
    Column("groupId", BigInteger, ForeignKey("groups.id"), primary_key=True),
    Column("accountId", BigInteger, primary_key=True),
    Index("ix_group_users_accountId", "accountId"),
)
