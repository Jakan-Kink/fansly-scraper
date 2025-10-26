"""added 2 more ContentTypes in the Enum

Revision ID: 7cc9ae5e798a
Revises: 00c9f171789c
Create Date: 2024-12-23 22:14:36.541985

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7cc9ae5e798a"
down_revision: str | None = "00c9f171789c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add AGGREGATED_POSTS and TIP_GOALS to the contenttype PostgreSQL ENUM
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'AGGREGATED_POSTS'")
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'TIP_GOALS'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # To downgrade, manually handle the enum if needed
    pass
