"""make media_locations.location nullable

Revision ID: bb7006ec7c0e
Revises: 3a230146cb14
Create Date: 2026-04-23 20:56:21.028206

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bb7006ec7c0e"
down_revision: str | None = "3a230146cb14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow NULL in media_locations.location.

    The Fansly API legitimately returns ``locations[{"location": None,
    "locationId": N}]`` for some media (e.g. Direct slots with no CDN path
    yet). Rejecting those at the DB level forced the whole Media payload
    to fail Pydantic validation.
    """
    with op.batch_alter_table("media_locations", schema=None) as batch_op:
        batch_op.alter_column(
            "location",
            existing_type=sa.String(),
            nullable=True,
        )


def downgrade() -> None:
    """Restore NOT NULL on media_locations.location.

    The downgrade will fail if any NULL rows have been persisted since the
    upgrade ran; that is intentional — the operator must resolve them
    before reverting the schema.
    """
    with op.batch_alter_table("media_locations", schema=None) as batch_op:
        batch_op.alter_column(
            "location",
            existing_type=sa.String(),
            nullable=False,
        )
