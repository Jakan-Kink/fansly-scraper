"""add unique constraint to media_locations table

Revision ID: 671bc5c3096d
Revises: 0e79e34eea22
Create Date: 2026-01-11 16:26:33.635906

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "671bc5c3096d"
down_revision: str | None = "0e79e34eea22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "media_locations_mediaId_locationId_key",
        "media_locations",
        ["mediaId", "locationId"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "media_locations_mediaId_locationId_key", "media_locations", type_="unique"
    )
