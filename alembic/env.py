from sqlalchemy.engine import Engine

from alembic import context
from config import FanslyConfig, load_config
from metadata import Base, Database

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", "sqlite:///metadata.db")

target_metadata = Base.metadata


def get_sync_engine() -> Engine:
    config = FanslyConfig(program_version="0.10.0")
    load_config(config)
    database = Database(config)
    engine = database.sync_engine
    engine.echo = True
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
