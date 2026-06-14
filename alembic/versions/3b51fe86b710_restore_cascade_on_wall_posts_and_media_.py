"""restore cascade on wall_posts and media_locations fks

The merge_recent_migrations revision recreated the wall_posts and
media_locations foreign keys without the ON DELETE CASCADE the original
constraints had. On PostgreSQL the constraints are always enforced, so
deleting a wall that still has wall_posts rows raises:

  ForeignKeyViolationError: update or delete on table "walls" violates
  foreign key constraint "fk_wall_posts_wall_id_new" on table "wall_posts"

(issue #109). This migration restores CASCADE on all three constraints,
matching the declarations in metadata/tables.py. Junction rows are removed
with their owner; referenced posts/media rows are untouched. Pre-existing
orphan junction rows (accrued while the constraint was absent / non-CASCADE)
are deleted before each FK is recreated, so the constraint can be added on a
live database that already has them.

Revision ID: 3b51fe86b710
Revises: bb7006ec7c0e
Create Date: 2026-06-12 14:58:50.390167

"""

from collections.abc import Sequence

from sqlalchemy import inspect

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3b51fe86b710"
down_revision: str | None = "bb7006ec7c0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, referred table, upgraded name, downgraded name)
# Downgrade must restore the fk_*_new names merge_recent_migrations created —
# its downgrade drops them by name, so a full-chain downgrade breaks otherwise.
_FKS: list[tuple[str, str, str, str, str]] = [
    (
        "wall_posts",
        "wallId",
        "walls",
        "wall_posts_wallId_fkey",
        "fk_wall_posts_wall_id_new",
    ),
    (
        "wall_posts",
        "postId",
        "posts",
        "wall_posts_postId_fkey",
        "fk_wall_posts_post_id_new",
    ),
    (
        "media_locations",
        "mediaId",
        "media",
        "media_locations_mediaId_fkey",
        "fk_media_locations_media_id_new",
    ),
]


def _recreate_fks(*, ondelete: str | None, downgrading: bool) -> None:
    """Drop each FK in _FKS (found by constrained column, since names vary
    between migrated and freshly-created databases) and recreate it."""
    conn = op.get_bind()
    inspector = inspect(conn)

    for table, column, referred_table, up_name, down_name in _FKS:
        existing = [
            name
            for fk in inspector.get_foreign_keys(table)
            if fk["constrained_columns"] == [column]
            and (name := fk["name"]) is not None
        ]
        for name in existing:
            op.drop_constraint(name, table, type_="foreignkey")
        # Remove orphan junction rows whose FK target no longer exists. They
        # accrued while the constraint was absent / non-CASCADE and would block
        # the constraint from being (re)created. This realizes the CASCADE
        # intent retroactively: a junction row goes with its owner. No-op on
        # clean databases (fresh installs, test DBs).
        op.execute(
            f'DELETE FROM "{table}" AS t '
            f'WHERE t."{column}" IS NOT NULL '
            f"AND NOT EXISTS "
            f'(SELECT 1 FROM "{referred_table}" AS r WHERE r.id = t."{column}")'
        )
        op.create_foreign_key(
            down_name if downgrading else up_name,
            table,
            referred_table,
            [column],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    """Restore ON DELETE CASCADE on wall_posts and media_locations FKs."""
    _recreate_fks(ondelete="CASCADE", downgrading=False)


def downgrade() -> None:
    """Recreate the FKs without CASCADE under their pre-upgrade names."""
    _recreate_fks(ondelete=None, downgrading=True)
