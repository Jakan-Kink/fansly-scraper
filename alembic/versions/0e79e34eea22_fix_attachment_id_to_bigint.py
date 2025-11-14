"""fix attachment id to bigint

Revision ID: 0e79e34eea22
Revises: b8dcecc1e979
Create Date: 2025-11-14 02:16:37.592374

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e79e34eea22'
down_revision: str | None = 'b8dcecc1e979'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Convert attachments.id from INTEGER to BIGINT to support large snowflake IDs
    op.alter_column(
        "attachments",
        "id",
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        autoincrement=True,
    )
    # contentId is already BigInteger, postId and messageId are already BigInteger


def downgrade() -> None:
    # Revert attachments.id from BIGINT to INTEGER
    op.alter_column(
        "attachments",
        "id",
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=False,
        autoincrement=True,
    )
