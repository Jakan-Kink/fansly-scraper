import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b4e07e00b3f8"
down_revision = "a4e07e00b3f7"
branch_labels = None
depends_on = None


def upgrade():
    """Add wall_posts table with foreign key constraints.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """
    # PostgreSQL: Foreign key behavior is controlled at constraint level (DEFERRABLE, etc.)
    # No PRAGMA equivalent needed

    # Create wall_posts table
    op.create_table(
        "wall_posts",
        sa.Column("wallId", sa.Integer(), nullable=False),
        sa.Column("postId", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["wallId"],
            ["walls.id"],
            name="fk_wall_posts_wall_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
            name="fk_wall_posts_post_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("wallId", "postId"),
    )


def downgrade():
    """Remove wall_posts table.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level.
    """
    # PostgreSQL: No PRAGMA equivalent needed

    # Drop wall_posts table
    op.drop_table("wall_posts")
