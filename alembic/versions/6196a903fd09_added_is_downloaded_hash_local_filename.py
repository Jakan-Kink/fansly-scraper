"""added is_downloaded, hash, local_filename

Revision ID: 6196a903fd09
Revises: 7cc9ae5e798a
Create Date: 2024-12-24 20:06:50.834613

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6196a903fd09"
down_revision: str | None = "7cc9ae5e798a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media") as batch_op:
        batch_op.add_column(sa.Column("local_filename", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_downloaded",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index("ix_media_content_hash", ["content_hash"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [i["name"] for i in inspector.get_indexes("media")]

    with op.batch_alter_table("media") as batch_op:
        if "ix_media_content_hash" in indexes:
            batch_op.drop_index("ix_media_content_hash")
        batch_op.drop_column("is_downloaded")
        batch_op.drop_column("content_hash")
        batch_op.drop_column("local_filename")
