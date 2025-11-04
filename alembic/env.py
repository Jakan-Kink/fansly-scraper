import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, pool

from alembic import context


config = context.config

# Construct database URL from environment variables if not provided
if not config.get_main_option("sqlalchemy.url"):
    pg_host = os.getenv("FANSLY_PG_HOST", "localhost")
    pg_port = os.getenv("FANSLY_PG_PORT", "5432")
    pg_user = os.getenv("FANSLY_PG_USER", "fansly_user")
    pg_password = os.getenv("FANSLY_PG_PASSWORD", "")
    pg_database = os.getenv("FANSLY_PG_DATABASE", "fansly")

    # URL-encode password to handle special characters
    password_encoded = quote_plus(pg_password) if pg_password else ""
    db_url = (
        f"postgresql://{pg_user}:{password_encoded}@{pg_host}:{pg_port}/{pg_database}"
    )
    config.set_main_option("sqlalchemy.url", db_url)

# Import Base directly to avoid circular imports through metadata/__init__.py
# This is safe for migrations since we only need the metadata, not the full app
try:
    from metadata.base import Base

    target_metadata = Base.metadata
except ImportError:
    # Fallback if direct import fails
    from metadata import Base

    target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise ValueError("No database URL configured in alembic.ini or environment")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    # Check if connection was passed in via Database class
    if context.config.attributes.get("connection") is None:
        # Create engine from config URL
        url = config.get_main_option("sqlalchemy.url")
        if not url:
            raise ValueError("No database URL configured in alembic.ini or environment")

        connectable = create_engine(
            url,
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            do_run_migrations(connection)
    else:
        # Use provided connection
        connection = context.config.attributes["connection"]
        do_run_migrations(connection)


def do_run_migrations(connection):
    """Run migrations given a connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # Detect column type changes
        compare_server_default=True,  # Detect server default changes
    )

    with context.begin_transaction():
        context.run_migrations()

    # Commit the transaction
    if connection.in_transaction():
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
