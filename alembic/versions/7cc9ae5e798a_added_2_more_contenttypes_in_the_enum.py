"""added 2 more ContentTypes in the Enum

Revision ID: 7cc9ae5e798a
Revises: 00c9f171789c
Create Date: 2024-12-23 22:14:36.541985

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7cc9ae5e798a"
down_revision: str | None = "00c9f171789c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Updated ContentType values
new_enum_values = (
    "ACCOUNT_MEDIA",
    "ACCOUNT_MEDIA_BUNDLE",
    "POLL",
    "STORY",
    "AGGREGATED_POSTS",
    "TIP_GOALS",
)


def upgrade() -> None:
    # 1. Create a temporary table with the updated CHECK constraint
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
    old_enum_values = ("ACCOUNT_MEDIA", "ACCOUNT_MEDIA_BUNDLE", "POLL", "STORY")

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

    # 2. Copy data back to the temporary table, excluding new values
    op.execute(
        """
        INSERT INTO attachments_tmp (id, postId, messageId, contentId, pos, contentType)
        SELECT id, postId, messageId, contentId, pos, contentType
        FROM attachments
        WHERE contentType IN ('ACCOUNT_MEDIA', 'ACCOUNT_MEDIA_BUNDLE', 'POLL', 'STORY')
    """
    )

    # 3. Drop the current table
    op.drop_table("attachments")

    # 4. Rename the temporary table back to the original table name
    op.rename_table("attachments_tmp", "attachments")
