"""add a new option for the ContentType Enum: POLL

Revision ID: b2f528bacfd3
Revises: 4b98a29f4965
Create Date: 2024-12-16 22:44:59.731127

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers
revision: str = "4b98a29f4965"
down_revision: str | None = "84146fdb359d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add POLL to the contenttype PostgreSQL ENUM
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'POLL'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # To downgrade, we would need to:
    # 1. Create a new enum without POLL
    # 2. Alter the column to use the new enum
    # 3. Drop the old enum
    # Since this is complex and rarely needed, we'll just pass
    # If you need to downgrade, manually handle the enum
    pass
