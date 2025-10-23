import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a4e07e00b3f7"
down_revision = "b2f528bacfd3"
branch_labels = None
depends_on = None


def upgrade():
    """Add media locations table and flags.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """
    # PostgreSQL: Foreign key behavior is controlled at constraint level (DEFERRABLE, etc.)
    # No PRAGMA equivalent needed

    # Add flags and meta_info columns to media table
    op.add_column("media", sa.Column("flags", sa.Integer(), nullable=True))
    op.add_column("media", sa.Column("meta_info", sa.String(), nullable=True))

    # Create media_locations table
    op.create_table(
        "media_locations",
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("locationId", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
            name="fk_media_locations_media_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mediaId", "locationId"),
        sa.UniqueConstraint("mediaId", "locationId", name="uq_media_locations"),
    )


def downgrade():
    """Remove media locations table and flags.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level.
    """
    # PostgreSQL: No PRAGMA equivalent needed

    # Drop media_locations table
    op.drop_table("media_locations")

    # Remove columns from media table
    op.drop_column("media", "meta_info")
    op.drop_column("media", "flags")
