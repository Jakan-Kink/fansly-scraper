import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context
from config import FanslyConfig
from metadata import Base, Database

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# if config.config_file_name is not None:
#     fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_engine() -> AsyncEngine:
    config = FanslyConfig(program_version="0.10.0")
    database = Database(config)
    return database.engine


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


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    engine = get_engine()

    async with engine.begin() as connection:
        await connection.run_sync(do_run_migrations)


def do_run_migrations(connection):
    """Run migrations given a connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    try:
        # Try to get the running loop
        loop = asyncio.get_running_loop()
        # If we are in an active loop, directly await the migration
        loop.create_task(
            run_migrations_online()
        )  # Ensure the coroutine is awaited in the running loop
    except RuntimeError:  # No event loop, we can use asyncio.run
        asyncio.run(run_migrations_online())
