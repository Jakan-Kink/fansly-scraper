"""fix timestamp columns to timestamptz

All timestamp columns were created as 'timestamp without time zone' despite
SA definitions using DateTime(timezone=True). asyncpg's timestamp encoder
uses a naive epoch, so writing aware (UTC) datetimes to these columns fails
with: "can't subtract offset-naive and offset-aware datetimes".

Fix: dynamically find ALL 'timestamp without time zone' columns in the
public schema and ALTER them to 'timestamp with time zone', treating
existing naive values as UTC.

Revision ID: f1a2b3c4d5e6
Revises: e4a1f7e15836
Create Date: 2026-03-12
"""

from sqlalchemy import text

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e4a1f7e15836"
branch_labels: tuple | None = None
depends_on: tuple | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure session is UTC so AT TIME ZONE 'UTC' is a no-op for
    # values that were already stored as UTC.
    conn.execute(text("SET timezone = 'UTC'"))

    rows = conn.execute(
        text(
            "SELECT table_name, column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "  AND data_type = 'timestamp without time zone'"
        )
    ).fetchall()

    for table_name, column_name in rows:
        op.execute(
            f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" '
            f"TYPE TIMESTAMP WITH TIME ZONE "
            f"USING \"{column_name}\" AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    # No downgrade — converting back would lose timezone information.
    pass
