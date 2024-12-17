"""add a new option for the ContentType Enum: POLL

Revision ID: b2f528bacfd3
Revises: 4b98a29f4965
Create Date: 2024-12-16 22:44:59.731127

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "4b98a29f4965"
down_revision: str | None = "84146fdb359d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Updated ContentType Enum (CHECK constraint will validate it)
new_enum_values = ("ACCOUNT_MEDIA", "ACCOUNT_MEDIA_BUNDLE", "POLL")


def upgrade() -> None:
    # 1. Create a temporary table with the updated constraint
    op.create_table(
        "attachments_tmp",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("postId", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column(
            "messageId", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True
        ),
        sa.Column("contentId", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column(
            "contentType",
            sa.String(),
            nullable=True,
            # Add CHECK constraint for the updated enum
            server_default=None,
        ),
        sa.CheckConstraint(
            f"contentType IN {new_enum_values}",
            name="ck_contenttype_enum",
        ),
        sa.CheckConstraint(
            "(postId IS NULL OR messageId IS NULL)",
            name="check_post_or_message_exclusivity",
        ),
    )

    # 2. Copy data from the old table to the temporary table
    op.execute(
        """
        INSERT INTO attachments_tmp (id, postId, messageId, contentId, pos, contentType)
        SELECT id, postId, messageId, contentId, pos, contentType
        FROM attachments
    """
    )

    # 3. Drop the old table
    op.drop_table("attachments")

    # 4. Rename the temporary table to the original table name
    op.rename_table("attachments_tmp", "attachments")


def downgrade() -> None:
    old_enum_values = ("ACCOUNT_MEDIA", "ACCOUNT_MEDIA_BUNDLE")

    # 1. Create a temporary table with the old CHECK constraint
    op.create_table(
        "attachments_tmp",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("postId", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column(
            "messageId", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True
        ),
        sa.Column("contentId", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column(
            "contentType",
            sa.String(),
            nullable=True,
            server_default=None,
        ),
        sa.CheckConstraint(
            f"contentType IN {old_enum_values}",
            name="ck_contenttype_enum",
        ),
        sa.CheckConstraint(
            "(postId IS NULL OR messageId IS NULL)",
            name="check_post_or_message_exclusivity",
        ),
    )

    # 2. Copy data from the current table to the temporary table
    op.execute(
        """
        INSERT INTO attachments_tmp (id, postId, messageId, contentId, pos, contentType)
        SELECT id, postId, messageId, contentId, pos, contentType
        FROM attachments
        WHERE contentType IN ('ACCOUNT_MEDIA', 'ACCOUNT_MEDIA_BUNDLE')
    """
    )

    # 3. Drop the current table
    op.drop_table("attachments")

    # 4. Rename the temporary table back to the original table name
    op.rename_table("attachments_tmp", "attachments")
