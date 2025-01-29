from sqlalchemy.engine import Engine

from alembic import context
from config import FanslyConfig, load_config
from metadata import Base, Database

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", "sqlite:///:memory:")

target_metadata = Base.metadata


def get_sync_engine(creator_name: str | None = None) -> Engine:
    """Get the sync engine for migrations.

    This should only be used when we don't have a connection passed in
    via alembic_cfg.attributes["connection"].

    Args:
        creator_name: Optional creator name for separate memory spaces

    Returns:
        SQLAlchemy engine configured for the appropriate memory space
    """
    from sqlalchemy import create_engine

    # Use appropriate shared memory URI
    if creator_name:
        # Use creator-specific shared memory
        safe_name = "".join(c if c.isalnum() else "_" for c in creator_name)
        uri = f"sqlite:///file:creator_{safe_name}?mode=memory&cache=shared"
    else:
        # Use global shared memory
        uri = "sqlite:///file:global_db?mode=memory&cache=shared"

    engine = create_engine(
        uri,
        echo=True,
        connect_args={"uri": True},  # Required for shared memory URIs
    )
    return engine


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
        dialect_name="sqlite",  # Explicitly specify SQLite as the dialect
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    if context.config.attributes.get("connection") is None:
        engine = get_sync_engine()
        connection = engine.connect()
    else:
        connection = context.config.attributes["connection"]

    with connection:
        do_run_migrations(connection)


def do_run_migrations(connection):
    """Run migrations given a connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        dialect_name="sqlite",  # Explicitly specify SQLite as the dialect
        compare_type=True,  # Detect column type changes
        compare_server_default=True,  # Detect server default changes
    )

    with context.begin_transaction():
        context.run_migrations()
    connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
