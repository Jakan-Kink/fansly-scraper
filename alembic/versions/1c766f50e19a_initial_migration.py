"""Initial migration

Revision ID: 1c766f50e19a
Revises: None
Create Date: 2024-12-15 23:38:15.221325

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c766f50e19a"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial database schema with foreign keys disabled.

    Note: Foreign keys are intentionally left disabled after this migration
    because the API data often includes references before the referenced objects.
    For example:
    1. A message might reference a user ID before we have that user's data
    2. A media item might reference a bundle before the bundle is created
    3. A post might reference media that hasn't been fetched yet

    The application handles data integrity at the business logic level by:
    1. Tracking which objects exist and which are pending
    2. Retrying failed operations when referenced objects become available
    3. Cleaning up any orphaned references during maintenance
    """
    conn = op.get_bind()
    # Disable foreign keys and keep them disabled
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("displayName", sa.String(), nullable=True),
        sa.Column("flags", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("about", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("following", sa.Boolean(), nullable=True),
        sa.Column("profileAccess", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("createdBy", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["createdBy"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("mimetype", sa.String(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("type", sa.Integer(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("fypFlag", sa.Integer(), nullable=True),
        sa.Column("inReplyTo", sa.Integer(), nullable=True),
        sa.Column("inReplyToRoot", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "timeline_stats",
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("imageCount", sa.Integer(), nullable=True),
        sa.Column("videoCount", sa.Integer(), nullable=True),
        sa.Column("bundleCount", sa.Integer(), nullable=True),
        sa.Column("bundleImageCount", sa.Integer(), nullable=True),
        sa.Column("bundleVideoCount", sa.Integer(), nullable=True),
        sa.Column("fetchedAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("accountId"),
    )
    op.create_table(
        "walls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "account_avatar",
        sa.Column("accountId", sa.Integer(), nullable=True),
        sa.Column("mediaId", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.UniqueConstraint("accountId", "mediaId"),
    )
    op.create_table(
        "account_banner",
        sa.Column("accountId", sa.Integer(), nullable=True),
        sa.Column("mediaId", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.UniqueConstraint("accountId", "mediaId"),
    )
    op.create_table(
        "account_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("previewId", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("access", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.ForeignKeyConstraint(
            ["previewId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("id", "accountId", "mediaId"),
    )
    op.create_table(
        "account_media_bundles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("previewId", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("access", sa.Boolean(), nullable=False),
        sa.Column("purchased", sa.Boolean(), nullable=False),
        sa.Column("whitelisted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["previewId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "group_users",
        sa.Column("groupId", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["groupId"],
            ["groups.id"],
        ),
        sa.PrimaryKeyConstraint("groupId", "accountId"),
    )
    op.create_table(
        "media_varients",
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("varientId", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.ForeignKeyConstraint(
            ["varientId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("mediaId", "varientId"),
        sa.UniqueConstraint("mediaId", "varientId"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("groupId", sa.Integer(), nullable=True),
        sa.Column("senderId", sa.Integer(), nullable=False),
        sa.Column("recipientId", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["groupId"],
            ["groups.id"],
        ),
        sa.ForeignKeyConstraint(
            ["recipientId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["senderId"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pinned_posts",
        sa.Column("postId", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("postId", "accountId"),
    )
    op.create_table(
        "post_mentions",
        sa.Column("postId", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("handle", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("postId", "accountId"),
        sa.UniqueConstraint("postId", "accountId"),
    )
    op.create_table(
        "account_media_bundle_media",
        sa.Column("bundle_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["account_media_bundles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["media_id"], ["account_media.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bundle_id", "media_id"),
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("postId", sa.Integer(), nullable=True),
        sa.Column("messageId", sa.Integer(), nullable=True),
        sa.Column("contentId", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column(
            "contentType",
            sa.Enum("ACCOUNT_MEDIA", "ACCOUNT_MEDIA_BUNDLE", name="contenttype"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(postId IS NULL OR messageId IS NULL)",
            name="check_post_or_message_exclusivity",
        ),
        sa.ForeignKeyConstraint(
            ["messageId"],
            ["messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Note: Foreign keys are intentionally left disabled
    # ### end Alembic commands ###


def downgrade() -> None:
    """Remove all tables and indexes."""
    conn = op.get_bind()
    # Disable foreign keys to avoid issues during table removal
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("attachments")
    op.drop_table("account_media_bundle_media")
    op.drop_table("post_mentions")
    op.drop_table("pinned_posts")
    op.drop_table("messages")
    op.drop_table("media_varients")
    op.drop_table("group_users")
    op.drop_table("account_media_bundles")
    op.drop_table("account_media")
    op.drop_table("account_banner")
    op.drop_table("account_avatar")
    op.drop_table("walls")
    op.drop_table("timeline_stats")
    op.drop_table("posts")
    op.drop_table("media")
    op.drop_table("groups")
    op.drop_table("accounts")
    # ### end Alembic commands ###
