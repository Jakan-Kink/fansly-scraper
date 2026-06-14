"""merge fork-main + tests-to-100 migration heads

Revision ID: b6d60f698c27
Revises: 3b51fe86b710, 5d3befc60c19
Create Date: 2026-06-14 16:30:08.382039

"""

from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "b6d60f698c27"
down_revision: str | None = ("3b51fe86b710", "5d3befc60c19")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
