"""Add id primary key to post_mentions

Previously post_mentions used postId as PK (a bug — only one mention per
post was possible). This adds an auto-increment id as the true PK and
adds unique constraints for deduplication.

Revision ID: e4a1f7e15836
Revises: 671bc5c3096d
Create Date: 2026-03-12 03:38:50.535872

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e4a1f7e15836"
down_revision: str | None = "671bc5c3096d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _exists(conn, query: str, params: dict) -> bool:
    return conn.execute(sa.text(query), params).scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # Add auto-increment id column as new PK
    if not _exists(
        conn,
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'post_mentions' AND column_name = 'id'",
        {},
    ):
        # Drop old PK (postId-only)
        if _exists(
            conn,
            "SELECT 1 FROM pg_constraint WHERE conname = 'post_mentions_pkey'",
            {},
        ):
            op.drop_constraint("post_mentions_pkey", "post_mentions", type_="primary")

        # Use raw SQL for SERIAL column (creates sequence automatically)
        op.execute("ALTER TABLE post_mentions ADD COLUMN id SERIAL PRIMARY KEY")

    # Unique constraints (idempotent)
    for name, cols in [
        ("uix_post_mentions_account", ["postId", "accountId"]),
        ("uix_post_mentions_handle", ["postId", "handle"]),
    ]:
        if not _exists(
            conn, "SELECT 1 FROM pg_constraint WHERE conname = :n", {"n": name}
        ):
            op.create_unique_constraint(name, "post_mentions", cols)

    # Indexes (idempotent)
    for name, cols, where in [
        ("ix_post_mentions_accountId", ["accountId"], None),
        (
            "ix_post_mentions_account",
            ["postId", "accountId"],
            '"accountId" IS NOT NULL',
        ),
        ("ix_post_mentions_handle", ["postId", "handle"], '"handle" IS NOT NULL'),
    ]:
        if not _exists(
            conn, "SELECT 1 FROM pg_indexes WHERE indexname = :n", {"n": name}
        ):
            kw = {}
            if where:
                kw["postgresql_where"] = sa.text(where)
            op.create_index(name, "post_mentions", cols, **kw)


def downgrade() -> None:
    op.drop_index("ix_post_mentions_handle", "post_mentions", if_exists=True)
    op.drop_index("ix_post_mentions_account", "post_mentions", if_exists=True)
    op.drop_index("ix_post_mentions_accountId", "post_mentions", if_exists=True)
    op.drop_constraint("uix_post_mentions_handle", "post_mentions", type_="unique")
    op.drop_constraint("uix_post_mentions_account", "post_mentions", type_="unique")
    op.drop_constraint("post_mentions_pkey", "post_mentions", type_="primary")
    op.drop_column("post_mentions", "id")
    op.create_primary_key("post_mentions_pkey", "post_mentions", ["postId"])
