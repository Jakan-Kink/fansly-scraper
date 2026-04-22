"""rename monitorstate lastseenat to lastcheckedat

Revision ID: 3a230146cb14
Revises: 7e027587c638
Create Date: 2026-04-17 03:00:03.175709

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3a230146cb14"
down_revision: str | None = "7e027587c638"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename monitor_state.lastSeenAtAtLastRun → lastCheckedAt.

    The new name reflects the semantic: this records when WE last verified
    the creator had no new content, not a snapshot of Account.lastSeenAt.
    """
    op.alter_column(
        "monitor_state",
        "lastSeenAtAtLastRun",
        new_column_name="lastCheckedAt",
    )


def downgrade() -> None:
    """Reverse the rename."""
    op.alter_column(
        "monitor_state",
        "lastCheckedAt",
        new_column_name="lastSeenAtAtLastRun",
    )
