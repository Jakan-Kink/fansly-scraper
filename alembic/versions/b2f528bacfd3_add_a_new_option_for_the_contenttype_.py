"""add a new option for the ContentType Enum: STORY

Revision ID: b2f528bacfd3
Revises: 4b98a29f4965
Create Date: 2024-12-16 23:06:59.731127

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers
revision: str = "b2f528bacfd3"  # Replace with generated ID
down_revision: str | None = "4b98a29f4965"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add STORY to the contenttype PostgreSQL ENUM
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'STORY'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # To downgrade, manually handle the enum if needed
    pass
