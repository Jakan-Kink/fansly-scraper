from sqlalchemy import create_engine, pool

from alembic import context

config = context.config

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
        connectable = create_engine(
            config.get_main_option("sqlalchemy.url"),
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
