"""Class-scoped EntityStore sharing ONE database across a whole test class.

The default ``entity_store`` fixture is function-scoped: every test that
requests it clones a fresh UUID Postgres database. For a class whose methods
only need the store as an in-memory/identity-map backing (or namespace their
writes by unique snowflake ids), that is one wasted ``CREATE DATABASE`` per
method. ``class_entity_store`` clones the template DB ONCE per class and shares
the store across the class's methods.

Usage — the class must run its async methods on the class-scoped event loop and
be pinned to one xdist worker (``--dist=loadgroup``), because the store's
asyncpg pool and the ``FanslyObject._store`` global singleton are shared::

    @pytest.mark.asyncio(loop_scope="class")
    @pytest.mark.xdist_group("my_class")
    class TestThing:
        async def test_a(self, class_entity_store): ...
        async def test_b(self, class_entity_store): ...

Methods that mutate store-global state (``_default_ttl``, ``set_ttl`` overrides,
cache contents) MUST reset it at the top of each method — the fixture yields
once per class, so state leaks across methods otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text

from config import FanslyConfig
from metadata import Database, PostgresEntityStore
from metadata.models import FanslyObject
from tests.fixtures.database.database_fixtures import _pg_connection_params


@pytest_asyncio.fixture(scope="class", loop_scope="class")
async def class_entity_store(
    pg_template_db: str,
) -> AsyncGenerator[PostgresEntityStore, None]:
    """One template-cloned database + EntityStore shared across a test class.

    Registered as the global singleton (``FanslyObject._store``) for the class,
    so code calling ``get_store()`` uses it. Skips (not fails) if Postgres is
    unavailable, mirroring ``uuid_test_db_factory``.
    """
    pg_host, pg_port, pg_user, pg_password, admin_url = _pg_connection_params()
    db_name = f"test_cls_{uuid.uuid4().hex[:8]}"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            # Terminate stragglers on the template before CREATE ... TEMPLATE
            # (Postgres holds ACCESS EXCLUSIVE during the clone).
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :tpl AND pid <> pg_backend_pid()"
                ),
                {"tpl": pg_template_db},
            )
            conn.execute(text(f"CREATE DATABASE {db_name} TEMPLATE {pg_template_db}"))
    except Exception as e:
        admin_engine.dispose()
        pytest.skip(
            f"PostgreSQL not available at {pg_host}:{pg_port} (user={pg_user}): {e}"
        )
    finally:
        admin_engine.dispose()

    config = FanslyConfig(program_version="0.14.5")
    config.pg_host = pg_host
    config.pg_port = pg_port
    config.pg_database = db_name
    config.pg_user = pg_user
    config.pg_password = pg_password

    db = Database(config, skip_migrations=True)
    store = await db.create_entity_store()
    try:
        yield store
    finally:
        FanslyObject._store = None
        if db._asyncpg_pool:
            await db._asyncpg_pool.close()
        db.close_sync()
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with suppress(Exception), admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            with suppress(Exception):
                conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
        admin_engine.dispose()


@pytest.fixture
def reset_class_store(class_entity_store: PostgresEntityStore) -> PostgresEntityStore:
    """Function-scoped view of ``class_entity_store`` with in-memory state cleared.

    Request this (instead of ``class_entity_store``) in shared-DB tests that
    exercise in-memory cache/identity-map/TTL behavior, so each method starts
    from an empty cache + default TTL config without paying for a fresh
    database. Clears the store's cache, type index, cache timestamps, per-type
    TTLs, and default TTL.
    """
    class_entity_store._default_ttl = None
    class_entity_store._type_ttls.clear()
    class_entity_store._cache.clear()
    class_entity_store._type_index.clear()
    class_entity_store._cache_timestamps.clear()
    return class_entity_store
