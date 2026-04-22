"""PostgresEntityStore — 2-tier entity store (local dict + asyncpg).

Architecture:
- Local dict for identity map (sync access for Pydantic model_validator)
- asyncpg pool for stateless DB transport (no sessions, no locks)

Filter syntax mirrors stash-graphql-client's StashEntityStore:
    find(Media, is_downloaded=True)              # EQUALS (default)
    find(Media, mimetype__contains="video")      # ILIKE
    find(Media, duration__gte=100)               # >=
    find(Hashtag, value__iexact="Test")          # case-insensitive exact
    find(Media, duration__between=(50, 200))     # BETWEEN
    find(Post, id__in=[1, 2, 3])                 # = ANY(...)
    find(Media, content_hash__null=True)          # IS NULL
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Any, TypeVar

import asyncpg

from config import db_logger
from helpers.rich_progress import get_progress_manager

from .models import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    FanslyObject,
    FanslyRecord,
    Group,
    Hashtag,
    Media,
    MediaLocation,
    MediaStoryState,
    Message,
    PinnedPost,
    Post,
    PostMention,
    Story,
    TimelineStats,
    Wall,
    get_from_cache_by_type_name,
)
from .tables import metadata as core_metadata


T = TypeVar("T", bound=FanslyObject)

# ── Type Registry ────────────────────────────────────────────────────────

_TYPE_REGISTRY: dict[str, type] = {
    cls.__name__: cls
    for cls in [
        Account,
        AccountMedia,
        AccountMediaBundle,
        Attachment,
        Group,
        Hashtag,
        Media,
        MediaLocation,
        MediaStoryState,
        Message,
        PinnedPost,
        Post,
        PostMention,
        Story,
        TimelineStats,
        Wall,
    ]
}

_TABLE_TO_MODEL: dict[str, type[FanslyObject]] = {
    cls.__table_name__: cls
    for cls in _TYPE_REGISTRY.values()
    if hasattr(cls, "__table_name__") and issubclass(cls, FanslyObject)
}

# ── Django-style filter infrastructure ───────────────────────────────────

# Cache-side comparators (Python operations on cached objects)
_CACHE_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "exact": lambda fv, v: fv == v,
    "contains": lambda fv, v: v in str(fv) if fv is not None else False,
    "icontains": lambda fv, v: (
        v.lower() in str(fv).lower() if fv is not None else False
    ),
    "iexact": lambda fv, v: (
        str(fv).lower() == str(v).lower() if fv is not None else False
    ),
    "gt": lambda fv, v: fv > v if fv is not None else False,
    "gte": lambda fv, v: fv >= v if fv is not None else False,
    "lt": lambda fv, v: fv < v if fv is not None else False,
    "lte": lambda fv, v: fv <= v if fv is not None else False,
    "ne": lambda fv, v: fv != v,
    "in": lambda fv, v: fv in v if isinstance(v, (list, tuple, set)) else False,
    "null": lambda fv, v: (fv is None) if v else (fv is not None),
    "between": lambda fv, v: (
        v[0] <= fv <= v[1]
        if fv is not None and isinstance(v, (list, tuple)) and len(v) == 2
        else False
    ),
}

# SQL-side operators (for asyncpg WHERE clauses)
_SQL_OPS: dict[str, str] = {
    "exact": "=",
    "contains": "ILIKE",
    "icontains": "ILIKE",
    "iexact": "ILIKE",  # handled specially in _build_where_clauses
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "ne": "!=",
    "in": "= ANY",
    "null": "IS NULL",
    "between": "BETWEEN",
}


class SortDirection(StrEnum):
    """Sort direction for find() queries."""

    ASC = "ASC"
    DESC = "DESC"


# Normalized form for order_by: list of (column_name, direction) tuples
OrderBySpec = list[tuple[str, SortDirection]]


def _normalize_order_by(
    order_by: str | tuple[str, SortDirection] | OrderBySpec | None,
) -> OrderBySpec:
    """Normalize order_by parameter to a list of (column, direction) tuples.

    Accepts:
        "createdAt"                          → [("createdAt", ASC)]
        ("createdAt", SortDirection.DESC)    → [("createdAt", DESC)]
        [("createdAt", DESC), ("id", ASC)]   → as-is
        None                                 → []
    """
    if order_by is None:
        return []
    if isinstance(order_by, str):
        return [(order_by, SortDirection.ASC)]
    if isinstance(order_by, tuple) and len(order_by) == 2:
        return [order_by]
    if isinstance(order_by, list):
        return order_by
    raise ValueError(f"Invalid order_by: {order_by!r}")


def _parse_lookup(key: str) -> tuple[str, str]:
    """Parse 'field__modifier' into (field_name, lookup).

    Supports nested paths via double-underscore (operator is always the last
    segment if it matches a known operator).
    """
    if "__" in key:
        field, lookup = key.rsplit("__", 1)
        if lookup in _CACHE_OPS:
            return field, lookup
    return key, "exact"


def _resolve_field_value(obj: Any, field_path: str) -> Any:
    """Resolve a potentially nested field path on an object.

    Simple fields return the attribute value directly.
    Nested paths (e.g., "attachments__contentId") traverse relationships:
    - When a list is encountered, returns a list of resolved values
      from each item (ANY-semantics handled by the caller).
    """
    if "__" not in field_path:
        return getattr(obj, field_path, None)

    parts = field_path.split("__")
    value: Any = obj
    for i, part in enumerate(parts):
        if value is None:
            return None
        if isinstance(value, list):
            remaining = "__".join(parts[i:])
            return [_resolve_field_value(item, remaining) for item in value]
        value = getattr(value, part, None)
    return value


def _matches_filters(
    obj: FanslyObject, parsed_filters: list[tuple[str, str, Any]]
) -> bool:
    """Check if a cached object matches all parsed Django-style filters.

    Supports nested field paths: when the resolved value is a list
    (from relationship traversal), uses ANY-semantics.
    """
    for field, lookup, value in parsed_filters:
        field_val = _resolve_field_value(obj, field)
        comparator = _CACHE_OPS.get(lookup)
        if comparator is None:
            return False
        if isinstance(field_val, list):
            if not any(comparator(item, value) for item in field_val):
                return False
        elif not comparator(field_val, value):
            return False
    return True


# ── PostgresEntityStore ──────────────────────────────────────────────────


class PostgresEntityStore:
    """Pydantic <-> asyncpg bridge.

    Replaces SQLAlchemy ORM sessions with stateless DB access,
    local dict caching, and Django-style filters.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        db_config: dict[str, Any] | None = None,
    ) -> None:
        self.pool = pool
        self._cache: dict[tuple[type, int], FanslyObject] = {}
        self._fully_loaded: set[type] = set()
        self._col_cache: dict[str, set[str]] = {}
        self._stats: dict[str, int] = defaultdict(int)

        # Thread-safe resource access: asyncpg pools bind internal asyncio
        # primitives to the event loop at creation time. Worker threads each
        # run their own asyncio.new_event_loop(), so shared resources would
        # fail with "Task got Future attached to a different loop".
        try:
            self._pool_loop: asyncio.AbstractEventLoop | None = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            self._pool_loop = None
        self._db_config = db_config
        self._thread_pools: dict[int, asyncpg.Pool] = {}
        self._thread_pool_lock = threading.Lock()

    def register_models(self) -> None:
        """Set this store as _store on all model classes."""
        FanslyObject._store = self

    # ── Thread-safe pool access ──────────────────────────────────────

    async def _get_pool(self) -> asyncpg.Pool:
        """Return the asyncpg pool for the current event loop.

        On the main loop returns self.pool directly. On a worker thread
        with a different event loop, lazily creates a per-thread pool.
        """
        current_loop = asyncio.get_running_loop()

        with self._thread_pool_lock:
            if self._pool_loop is None:
                self._pool_loop = current_loop

        if current_loop is self._pool_loop:
            return self.pool

        tid = threading.current_thread().ident
        if tid is None:  # pragma: no cover
            return self.pool
        with self._thread_pool_lock:
            if tid in self._thread_pools:
                return self._thread_pools[tid]

        if not self._db_config:
            raise RuntimeError(
                "PostgresEntityStore cannot create a thread-local pool: "
                "no db_config was provided at construction time."
            )

        pool = await asyncpg.create_pool(
            host=self._db_config["host"],
            port=self._db_config["port"],
            database=self._db_config["database"],
            user=self._db_config["user"],
            password=self._db_config["password"],
            min_size=1,
            max_size=3,
            command_timeout=30,
            init=self._init_pg_connection,
            setup=self._setup_pg_connection,
            reset=self._reset_pg_connection,
        )
        with self._thread_pool_lock:
            # Double-check: another coroutine may have created one while
            # we were awaiting create_pool (TOCTOU race).
            if tid in self._thread_pools:
                await pool.close()
                return self._thread_pools[tid]
            self._thread_pools[tid] = pool
        db_logger.info(
            f"Created thread-local asyncpg pool for thread {tid} "
            f"({threading.current_thread().name})"
        )
        return pool

    @staticmethod
    async def _init_pg_connection(conn: asyncpg.Connection) -> None:
        """Register JSONB codec and query logger on new pool connections.

        Runs once per new connection. Query loggers don't trigger asyncpg's
        release warning, so they stay in ``init``.
        """
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        from .logging_config import get_db_logger

        conn.add_query_logger(get_db_logger().query_logger_callback)

    @staticmethod
    async def _setup_pg_connection(conn: asyncpg.Connection) -> None:
        """Attach log listener on every acquire from pool.

        Paired with ``_reset_pg_connection`` which removes the log listener
        before each release, preventing asyncpg InterfaceWarning about
        active log listeners on pool release.
        """
        from .logging_config import DatabaseLogger

        conn.add_log_listener(DatabaseLogger.log_listener_callback)

    @staticmethod
    async def _reset_pg_connection(conn: asyncpg.Connection) -> None:
        """Remove log listener before connection is released to pool.

        asyncpg warns (InterfaceWarning) when a connection with active
        log listeners is returned to the pool. This ``reset`` callback
        removes it; ``_setup_pg_connection`` re-adds it on next acquire.
        """
        from .logging_config import DatabaseLogger

        # Listener may already be removed or was never added if setup didn't run
        with contextlib.suppress(ValueError):
            conn.remove_log_listener(DatabaseLogger.log_listener_callback)

    async def close_thread_resources(self) -> None:
        """Close all per-thread asyncpg pools."""
        with self._thread_pool_lock:
            pools = list(self._thread_pools.values())
            self._thread_pools.clear()
        for pool in pools:
            try:
                await pool.close()
            except Exception as e:
                db_logger.warning(f"Error closing thread pool: {e}")

    @staticmethod
    def _q(name: str) -> str:
        """Quote a SQL identifier to handle reserved words."""
        return f'"{name}"'

    # ── Column / Table Helpers ───────────────────────────────────────

    def _table_columns(self, table_name: str) -> set[str]:
        if table_name not in self._col_cache:
            table_def = core_metadata.tables.get(table_name)
            self._col_cache[table_name] = (
                {c.name for c in table_def.columns} if table_def is not None else set()
            )
        return self._col_cache[table_name]

    @staticmethod
    def _pk_column(model_type: type[FanslyObject]) -> str:
        """Get the primary key column name for a model type."""
        return getattr(model_type, "__pk_column__", "id")

    # ── Sync Cache (identity map) ────────────────────────────────────

    def get_from_cache(self, model_type: type[T], entity_id: int) -> T | None:
        """Sync cache lookup. Called by model_validator."""
        return self._cache.get((model_type, entity_id))  # type: ignore[return-value]

    def get_from_cache_by_type_name(
        self, type_name: str, entity_id: int
    ) -> FanslyObject | None:
        """Lookup by type name string."""
        return get_from_cache_by_type_name(self, type_name, entity_id)

    @staticmethod
    def get_type_by_name(type_name: str) -> type | None:
        """Resolve type name string to class. Used by identity map merge."""
        return _TYPE_REGISTRY.get(type_name)

    def cache_instance(self, obj: FanslyObject) -> None:
        """Add to local identity map."""
        if obj.id is not None:
            self._cache[(type(obj), obj.id)] = obj

    def is_fully_loaded(self, model_type: type) -> bool:
        return model_type in self._fully_loaded

    # ── In-memory filter (lambda predicate) ──────────────────────────

    def filter(
        self,
        model_type: type[T],
        predicate: Callable[[T], bool] | None = None,
    ) -> list[T]:
        """Filter cached objects with Python predicate. No DB query.

        For simple equality/comparison filters, prefer find() with kwargs.
        Use this only for complex predicates that can't be expressed as kwargs.
        """
        results: list[T] = []
        for (cached_type, _), obj in self._cache.items():
            if cached_type is not model_type:
                continue
            if predicate is None or predicate(obj):  # type: ignore[arg-type]
                results.append(obj)  # type: ignore[arg-type]
        self._stats["filter_cache_hits"] += len(results)
        self._stats["filter_empty"] += 0 if results else 1
        return results

    # ── Read (Django-style filters) ──────────────────────────────────

    async def get(self, model_type: type[T], entity_id: int) -> T | None:
        """Get by ID. Local cache -> DB."""
        cached = self._cache.get((model_type, entity_id))
        if cached is not None:
            self._stats["get_cache_hits"] += 1
            return cached  # type: ignore[return-value]

        row = await self._fetch_one_by_id(model_type, entity_id)
        if row is None:
            self._stats["get_misses"] += 1
            return None
        self._stats["get_pg_hits"] += 1
        data = self._prepare_row_data(model_type, dict(row))
        obj = model_type.model_validate(data)
        obj._is_new = False  # loaded from DB, not new
        return obj  # type: ignore[return-value]

    async def find(
        self,
        model_type: type[T],
        *,
        order_by: str | tuple[str, SortDirection] | OrderBySpec | None = None,
        **filters: Any,
    ) -> list[T]:
        """Search with Django-style filters. Cache-first if fully loaded.

        Args:
            order_by: Sort results. Accepts:
                - str: column name (ASC default)
                - tuple: (column, SortDirection)
                - list of tuples: multi-column sort
        """
        parsed = [(*_parse_lookup(k), v) for k, v in filters.items()]
        sort_spec = _normalize_order_by(order_by)
        if sort_spec:
            self._validate_order_by(model_type, sort_spec)

        if model_type in self._fully_loaded:
            results = [
                obj  # type: ignore[misc]
                for (ct, _), obj in self._cache.items()
                if ct is model_type and _matches_filters(obj, parsed)
            ]
            if sort_spec:
                results = self._sort_results(results, sort_spec)
            self._stats["find_cache_hits"] += len(results)
            return results

        rows = await self._query_with_filters(
            model_type,
            parsed,
            order_by=sort_spec,
        )
        self._stats["find_pg_hits"] += len(rows)
        results: list[T] = []
        for row in rows:
            obj = model_type.model_validate(
                self._prepare_row_data(model_type, dict(row))
            )
            obj._is_new = False  # loaded from DB
            results.append(obj)  # type: ignore[arg-type]
        return results

    async def find_one(
        self,
        model_type: type[T],
        *,
        order_by: str | tuple[str, SortDirection] | OrderBySpec | None = None,
        **filters: Any,
    ) -> T | None:
        """Find first match. Cache-first if fully loaded.

        With order_by, returns the first result after sorting
        (e.g., find_one(Media, order_by="createdAt") → oldest).
        """
        parsed = [(*_parse_lookup(k), v) for k, v in filters.items()]
        sort_spec = _normalize_order_by(order_by)
        if sort_spec:
            self._validate_order_by(model_type, sort_spec)

        if model_type in self._fully_loaded:
            if sort_spec:
                # Need to collect all matches, sort, then take first
                matches = [
                    obj  # type: ignore[misc]
                    for (ct, _), obj in self._cache.items()
                    if ct is model_type and _matches_filters(obj, parsed)
                ]
                if not matches:
                    return None
                sorted_matches = self._sort_results(matches, sort_spec)
                self._stats["find_one_cache_hits"] += 1
                return sorted_matches[0]
            for (ct, _), obj in self._cache.items():
                if ct is model_type and _matches_filters(obj, parsed):
                    self._stats["find_one_cache_hits"] += 1
                    return obj  # type: ignore[return-value]
            return None

        row = await self._query_with_filters(
            model_type,
            parsed,
            limit=1,
            order_by=sort_spec,
        )
        if not row:
            self._stats["find_one_pg_misses"] += 1
            return None
        self._stats["find_one_pg_hits"] += 1
        data = self._prepare_row_data(model_type, dict(row[0]))
        obj = model_type.model_validate(data)
        obj._is_new = False  # loaded from DB
        return obj  # type: ignore[return-value]

    async def count(self, model_type: type[T], **filters: Any) -> int:
        """Count matching objects."""
        parsed = [(*_parse_lookup(k), v) for k, v in filters.items()]

        if model_type in self._fully_loaded:
            return sum(
                1
                for (ct, _), obj in self._cache.items()
                if ct is model_type and _matches_filters(obj, parsed)
            )

        tbl = model_type.__table_name__
        sql = f"SELECT COUNT(*) FROM {tbl}"
        conditions, params, _idx = self._build_where_clauses(tbl, parsed)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        pool = await self._get_pool()
        return await pool.fetchval(sql, *params)

    async def find_iter(
        self,
        model_type: type[T],
        *,
        batch_size: int = 50,
        order_by: str | tuple[str, SortDirection] | OrderBySpec | None = None,
        **filters: Any,
    ) -> AsyncIterator[list[T]]:
        """Async generator yielding batches of matching objects.

        When using order_by with the SQL path, results are deterministically
        ordered — required for correct OFFSET pagination.
        """
        parsed = [(*_parse_lookup(k), v) for k, v in filters.items()]
        sort_spec = _normalize_order_by(order_by)
        if sort_spec:
            self._validate_order_by(model_type, sort_spec)

        if model_type in self._fully_loaded:
            all_matches = [
                obj  # type: ignore[misc]
                for (ct, _), obj in self._cache.items()
                if ct is model_type and _matches_filters(obj, parsed)
            ]
            if sort_spec:
                all_matches = self._sort_results(all_matches, sort_spec)
            for i in range(0, len(all_matches), batch_size):
                yield all_matches[i : i + batch_size]
            return

        offset = 0
        while True:
            rows = await self._query_with_filters(
                model_type,
                parsed,
                limit=batch_size,
                offset=offset,
                order_by=sort_spec,
            )
            if not rows:
                break
            batch: list[T] = []
            for row in rows:
                obj = model_type.model_validate(
                    self._prepare_row_data(model_type, dict(row))
                )
                obj._is_new = False  # loaded from DB
                batch.append(obj)  # type: ignore[arg-type]
            yield batch
            if len(rows) < batch_size:
                break
            offset += batch_size

    async def get_or_create(
        self,
        model_type: type[T],
        defaults: dict[str, Any] | None = None,
        **filters: Any,
    ) -> tuple[T, bool]:
        """Find or create. Handles race conditions."""
        existing = await self.find_one(model_type, **filters)
        if existing is not None:
            return existing, False

        merged = {**filters, **(defaults or {})}
        obj = model_type(**merged)
        obj._is_new = True  # Explicitly mark for snowflake IDs
        try:
            await self.save(obj)
        except asyncpg.UniqueViolationError:
            existing = await self.find_one(model_type, **filters)
            if existing is not None:
                return existing, False
            raise
        else:
            return obj, True  # type: ignore[return-value]

    async def get_many(self, model_type: type[T], entity_ids: list[int]) -> list[T]:
        """Batch get: local cache → Postgres with ANY($1)."""
        results: list[T] = []
        missing_ids: list[int] = []
        for eid in entity_ids:
            cached = self._cache.get((model_type, eid))
            if cached is not None:
                self._stats["get_many_cache_hits"] += 1
                results.append(cached)  # type: ignore[arg-type]
            else:
                missing_ids.append(eid)

        if not missing_ids:
            return results

        pk_col = self._pk_column(model_type)
        table_name = model_type.__table_name__
        sql = f"SELECT * FROM {table_name} WHERE {self._q(pk_col)} = ANY($1)"
        pool = await self._get_pool()
        rows = await pool.fetch(sql, missing_ids)
        self._stats["get_many_pg_hits"] += len(rows)
        self._stats["get_many_misses"] += len(missing_ids) - len(rows)
        for row in rows:
            data = self._prepare_row_data(model_type, dict(row))
            obj = model_type.model_validate(data)
            obj._is_new = False  # loaded from DB
            self.cache_instance(obj)
            results.append(obj)  # type: ignore[arg-type]

        return results

    # ── Write ────────────────────────────────────────────────────────

    async def save(self, obj: FanslyObject) -> None:
        """Write to DB + update cache.

        Save order (prevents FK constraint violations):
        1. Save related entities referenced by junction tables — they
           may have FK columns pointing back to this entity (e.g.,
           Media.accountId → accounts.id), so this entity must be
           saved first if it's new. We handle this by saving the
           entity's own row before recursing into related entities.
        2. INSERT/UPDATE the entity itself (scalar columns only,
           junction sync happens after related entities are saved).
        3. Sync junction table rows (both sides now exist in DB).
        """
        # Step 1: INSERT/UPDATE entity row (scalars only, no junctions yet)
        if obj._is_new:
            await self._insert_row(obj)
            obj._is_new = False
        elif obj.is_dirty():
            await self._update(obj)

        # Step 2: Save related entities for junction tables.
        # Now that the parent entity exists in DB, related entities
        # can safely reference it via FK (e.g., Media.accountId).
        for field_name, meta in type(obj).__relationships__.items():
            if not meta.assoc_table:
                continue
            related = getattr(obj, field_name, None)
            if related is None:
                continue
            if meta.is_list and isinstance(related, list):
                for r in related:
                    if isinstance(r, FanslyObject) and r._is_new:
                        await self.save(r)
            elif isinstance(related, FanslyObject) and related._is_new:
                await self.save(related)

        # Step 3: Sync junction table rows (both sides now exist in DB)
        await self._sync_assoc_tables(obj)

        self.cache_instance(obj)
        obj.mark_clean()

    async def _insert_row(self, obj: FanslyObject) -> None:
        """INSERT scalar columns only (no junction sync)."""
        data = obj.to_db_dict()
        table_name = type(obj).__table_name__
        cols = self._table_columns(table_name)
        table_data = {k: v for k, v in data.items() if k in cols}

        if not table_data:
            return

        col_names = [self._q(k) for k in table_data]
        values = list(table_data.values())
        placeholders = [f"${i + 1}" for i in range(len(values))]
        pk_col = self._pk_column(type(obj))

        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            if obj.id is None and pk_col == "id":
                # Auto-increment (Hashtag): omit id column so PG generates it
                table_data.pop("id", None)
                col_names = [self._q(k) for k in table_data]
                values = list(table_data.values())
                placeholders = [f"${i + 1}" for i in range(len(values))]
                sql = (
                    f"INSERT INTO {table_name} ({', '.join(col_names)}) "
                    f"VALUES ({', '.join(placeholders)}) RETURNING id"
                )
                new_id = await conn.fetchval(sql, *values)
                obj.id = new_id
            else:
                # Snowflake ID or non-id PK: INSERT with provided ID
                sql = (
                    f"INSERT INTO {table_name} ({', '.join(col_names)}) "
                    f"VALUES ({', '.join(placeholders)})"
                )
                await conn.execute(sql, *values)

    async def _update(self, obj: FanslyObject) -> None:
        """UPDATE only changed scalar fields (no junction sync)."""
        changed = obj.get_changed_fields()
        if not changed:
            return

        db_data = obj.to_db_dict(only_dirty=True)
        pk_col = self._pk_column(type(obj))
        db_data.pop(pk_col, None)
        db_data.pop("id", None)

        # Filter to actual DB columns
        table_name = type(obj).__table_name__
        cols = self._table_columns(table_name)
        scalar_data = {k: v for k, v in db_data.items() if k in cols}

        if not scalar_data:
            return

        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            set_parts = []
            vals: list[Any] = []
            for i, (col, val) in enumerate(scalar_data.items(), 1):
                set_parts.append(f"{self._q(col)} = ${i}")
                vals.append(val)

            pk_value = getattr(obj, pk_col)
            vals.append(pk_value)
            sql = (
                f"UPDATE {table_name} SET {', '.join(set_parts)} "
                f"WHERE {self._q(pk_col)} = ${len(vals)}"
            )
            await conn.execute(sql, *vals)

    async def _sync_assoc_tables(self, obj: FanslyObject) -> None:
        """Sync all junction tables for an entity.

        Called by save() after the entity row and related entities are
        all in the DB. Determines which relationship fields changed
        and delegates to _sync_associations for the actual SQL.
        """
        rel_keys = set(obj.__relationships__.keys())
        changed = obj.get_changed_fields()
        rel_changes = {k for k in changed if k in rel_keys}

        # For new entities (just inserted), sync ALL assoc_table relationships
        # For existing entities, only sync changed ones
        if not rel_changes:
            # Check if any assoc_table relationships have data (new entity path)
            for field_name, meta in type(obj).__relationships__.items():
                if meta.assoc_table and getattr(obj, field_name, None) is not None:
                    rel_changes.add(field_name)

        if not rel_changes:
            return

        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._sync_associations(conn, obj, only_fields=rel_changes)

    async def _sync_associations(
        self,
        conn: asyncpg.Connection,
        obj: FanslyObject,
        only_fields: set[str] | None = None,
    ) -> None:
        """Sync M2M association tables (delta-based).

        Handles both list relationships (M2M) and scalar relationships
        (1:1 junction like account_avatar/account_banner).
        """
        for field_name, meta in type(obj).__relationships__.items():
            if not meta.assoc_table:
                continue
            if only_fields is not None and field_name not in only_fields:
                continue

            related = getattr(obj, field_name, None)

            assoc_def = core_metadata.tables[meta.assoc_table]
            col_names = [c.name for c in assoc_def.columns]
            # Use fk_column (when set) to determine owner FK deterministically,
            # rather than relying on column definition order.
            if meta.fk_column and meta.fk_column in col_names:
                owner_fk = meta.fk_column
                related_fk = next(c for c in col_names if c != owner_fk)
            else:
                owner_fk, related_fk = col_names[0], col_names[1]
            q_owner = self._q(owner_fk)
            q_related = self._q(related_fk)

            if not meta.is_list:
                # Scalar 1:1 junction (avatar, banner): replace
                await conn.execute(
                    f"DELETE FROM {meta.assoc_table} WHERE {q_owner} = $1",
                    obj.id,
                )
                if related is not None:
                    rid = FanslyObject._get_id(related)
                    if rid is not None:
                        await conn.execute(
                            f"INSERT INTO {meta.assoc_table} "
                            f"({q_owner}, {q_related}) "
                            f"VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            obj.id,
                            rid,
                        )
                continue

            if not isinstance(related, list):
                continue

            # Distinguish entity relationships (have .id) from record
            # relationships (FanslyRecord with scalar data, no .id)
            first = next(iter(related), None)
            has_ids = first is not None and FanslyObject._get_id(first) is not None

            if has_ids and meta.ordered:
                # Ordered entity junction (e.g., account_media_bundle_media):
                # DELETE all + re-INSERT with pos = list index
                await conn.execute(
                    f"DELETE FROM {meta.assoc_table} WHERE {q_owner} = $1",
                    obj.id,
                )
                for pos, r in enumerate(related):
                    rid = FanslyObject._get_id(r)
                    if rid is not None:
                        await conn.execute(
                            f"INSERT INTO {meta.assoc_table} "
                            f'({q_owner}, {q_related}, "pos") '
                            f"VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                            obj.id,
                            rid,
                            pos,
                        )
            elif has_ids:
                # Entity junction (e.g., media_variants): delta-based sync
                new_ids = {
                    FanslyObject._get_id(r)
                    for r in related
                    if FanslyObject._get_id(r) is not None
                }
                existing_rows = await conn.fetch(
                    f"SELECT {q_related} FROM {meta.assoc_table} WHERE {q_owner} = $1",
                    obj.id,
                )
                existing_ids = {row[related_fk] for row in existing_rows}

                to_add = new_ids - existing_ids
                if to_add:
                    await conn.executemany(
                        f"INSERT INTO {meta.assoc_table} ({q_owner}, {q_related}) "
                        f"VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        [(obj.id, rid) for rid in to_add],
                    )
                to_remove = existing_ids - new_ids
                if to_remove:
                    await conn.execute(
                        f"DELETE FROM {meta.assoc_table} "
                        f"WHERE {q_owner} = $1 AND {q_related} = ANY($2)",
                        obj.id,
                        list(to_remove),
                    )
            else:
                # Record relationship (e.g., media_locations): replace all.
                # Pre-create stubs for any FK targets that don't exist yet,
                # then wrap the DELETE+INSERT in a savepoint as a safety net.
                table_cols = set(col_names)
                all_rows = []
                for r in related:
                    row = {k: v for k, v in r.model_dump().items() if k in table_cols}
                    row[owner_fk] = obj.id
                    all_rows.append(row)

                # Create stubs for missing FK targets (#51)
                await self._ensure_junction_fk_targets(
                    meta.assoc_table, all_rows, owner_fk
                )

                try:
                    async with conn.transaction():
                        await conn.execute(
                            f"DELETE FROM {meta.assoc_table} WHERE {q_owner} = $1",
                            obj.id,
                        )
                        for row in all_rows:
                            cols = ", ".join(self._q(k) for k in row)
                            vals = ", ".join(f"${i}" for i in range(1, len(row) + 1))
                            await conn.execute(
                                f"INSERT INTO {meta.assoc_table} ({cols}) "
                                f"VALUES ({vals}) ON CONFLICT DO NOTHING",
                                *row.values(),
                            )
                except asyncpg.ForeignKeyViolationError as exc:  # pragma: no cover
                    # Safety net: if _ensure_junction_fk_targets couldn't
                    # resolve all FK targets (e.g., multi-FK junction with
                    # a target model that doesn't implement create_stub),
                    # the INSERT fails here. Log and continue rather than
                    # crashing the entire save. (#51)
                    db_logger.warning(
                        "Skipped %s junction sync for %r: %s",
                        meta.assoc_table,
                        obj,
                        exc,
                    )

    # ── FK stub creation ──────────────────────────────────────────────

    async def _ensure_junction_fk_targets(
        self,
        assoc_table: str,
        rows: list[dict[str, Any]],
        owner_fk: str,
    ) -> None:
        """Create stub entities for any FK targets missing from the DB.

        Inspects the junction table's FK columns (excluding the owner FK)
        and, for each referenced entity that doesn't exist, calls
        ``TargetModel.create_stub(entity_id, **row_context)``.

        If the target model hasn't implemented ``create_stub``, the
        ``StubNotImplementedError`` propagates to the caller, which can
        either provide additional context or let it reach the console.
        """
        table_def = core_metadata.tables.get(assoc_table)
        if table_def is None:
            return

        # Collect (column_name, target_table_name) for non-owner FK columns
        fk_targets: list[tuple[str, str]] = [
            (col.name, fk.column.table.name)
            for col in table_def.columns
            if col.name != owner_fk
            for fk in col.foreign_keys
        ]

        if not fk_targets:
            return

        for col_name, target_table in fk_targets:
            model_cls = _TABLE_TO_MODEL.get(target_table)
            if model_cls is None:  # pragma: no cover
                continue

            # Deduplicate: map target_id → first row that references it
            seen: dict[int, dict[str, Any]] = {}
            for row in rows:
                tid = row.get(col_name)
                if tid is not None and tid not in seen:
                    seen[tid] = row

            for target_id, row_context in seen.items():
                if self.get_from_cache(model_cls, target_id) is not None:
                    continue
                if await self._fetch_one_by_id(model_cls, target_id) is not None:
                    continue

                # Missing — create stub (raises StubNotImplementedError
                # if the model doesn't support it)
                stub = model_cls.create_stub(target_id, **row_context)
                await self._insert_row(stub)
                stub._is_new = False
                self.cache_instance(stub)

                from .stub_tracker import register_stub

                await register_stub(
                    target_table, target_id, reason=f"junction_fk:{assoc_table}"
                )

    # ── Ordered junction helpers ─────────────────────────────────────

    async def sync_junction(
        self,
        assoc_table: str,
        owner_fk: str,
        owner_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Sync a junction table: DELETE all for owner, then re-INSERT rows.

        Each row dict should contain all non-owner columns (e.g., related FK,
        pos, createdAt, etc.). The owner FK is injected automatically.

        Examples:
            # Simple junction (wall_posts)
            await store.sync_junction("wall_posts", "wallId", wall.id, [
                {"postId": 1}, {"postId": 2},
            ])

            # Ordered junction (account_media_bundle_media)
            await store.sync_junction("account_media_bundle_media", "bundle_id", bundle.id, [
                {"media_id": 10, "pos": 0}, {"media_id": 20, "pos": 1},
            ])

            # Junction with extra columns (pinned_posts)
            await store.sync_junction("pinned_posts", "accountId", account.id, [
                {"postId": 123, "pos": 0, "createdAt": dt1},
                {"postId": 456, "pos": 1, "createdAt": dt2},
            ])
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"DELETE FROM {assoc_table} WHERE {self._q(owner_fk)} = $1",
                owner_id,
            )
            if not rows:
                return
            # Build INSERT from first row's keys
            all_cols = [owner_fk, *rows[0].keys()]
            col_sql = ", ".join(self._q(c) for c in all_cols)
            placeholders = ", ".join(f"${i + 1}" for i in range(len(all_cols)))
            sql = (
                f"INSERT INTO {assoc_table} ({col_sql}) "
                f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            )
            row_keys = list(rows[0])
            await conn.executemany(
                sql,
                [(owner_id, *[row[k] for k in row_keys]) for row in rows],
            )

    # ── Delete ───────────────────────────────────────────────────────

    async def delete(self, obj: FanslyObject) -> None:
        """Delete from DB + evict from cache."""
        if obj.id is None:
            return
        pk_col = self._pk_column(type(obj))
        pk_value = getattr(obj, pk_col)
        table_name = type(obj).__table_name__
        pool = await self._get_pool()
        await pool.execute(
            f"DELETE FROM {table_name} WHERE {self._q(pk_col)} = $1",
            pk_value,
        )
        self.invalidate(type(obj), obj.id)

    async def delete_many(self, model_type: type[T], ids: list[int]) -> int:
        """Bulk delete by ID list. Returns count deleted."""
        if not ids:
            return 0
        pk_col = self._pk_column(model_type)
        table_name = model_type.__table_name__
        pool = await self._get_pool()
        await pool.execute(
            f"DELETE FROM {table_name} WHERE {self._q(pk_col)} = ANY($1)",
            ids,
        )
        for entity_id in ids:
            self.invalidate(model_type, entity_id)
        return len(ids)

    # ── FanslyRecord operations (composite PK) ───────────────────────

    async def insert_record(self, record: FanslyRecord) -> None:
        """INSERT a FanslyRecord (composite PK, no identity map)."""
        table_name = type(record).__table_name__
        cols = self._table_columns(table_name)
        data = record.model_dump()
        table_data = {k: v for k, v in data.items() if k in cols}

        col_names = [self._q(k) for k in table_data]
        values = list(table_data.values())
        placeholders = [f"${i + 1}" for i in range(len(values))]

        sql = (
            f"INSERT INTO {table_name} ({', '.join(col_names)}) "
            f"VALUES ({', '.join(placeholders)}) ON CONFLICT DO NOTHING"
        )
        pool = await self._get_pool()
        await pool.execute(sql, *values)

    async def delete_record(
        self,
        record_type: type[FanslyRecord],
        **pk_filters: Any,
    ) -> bool:
        """DELETE a FanslyRecord by composite PK."""
        table_name = record_type.__table_name__
        conditions = []
        params = []
        for i, (col, val) in enumerate(pk_filters.items(), 1):
            conditions.append(f"{self._q(col)} = ${i}")
            params.append(val)

        sql = f"DELETE FROM {table_name} WHERE {' AND '.join(conditions)}"
        pool = await self._get_pool()
        result = await pool.execute(sql, *params)
        return result != "DELETE 0"

    async def find_records(
        self,
        record_type: type[FanslyRecord],
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Query FanslyRecords. Returns raw dicts (no identity map)."""
        table_name = record_type.__table_name__
        sql = f"SELECT * FROM {table_name}"
        conditions = []
        params = []
        for i, (col, val) in enumerate(filters.items(), 1):
            conditions.append(f"{self._q(col)} = ${i}")
            params.append(val)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        pool = await self._get_pool()
        rows = await pool.fetch(sql, *params)
        return [dict(row) for row in rows]

    # ── Bulk operations ──────────────────────────────────────────────

    async def bulk_upsert(
        self,
        model_type: type[T],
        items: list[dict[str, Any]],
    ) -> None:
        """Bulk INSERT ... ON CONFLICT DO NOTHING for entity dicts."""
        if not items:
            return
        table_name = model_type.__table_name__
        cols = self._table_columns(table_name)

        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for item in items:
                table_data = {k: v for k, v in item.items() if k in cols}
                if not table_data:
                    continue
                col_names = [self._q(k) for k in table_data]
                values = list(table_data.values())
                placeholders = [f"${i + 1}" for i in range(len(values))]
                sql = (
                    f"INSERT INTO {table_name} ({', '.join(col_names)}) "
                    f"VALUES ({', '.join(placeholders)}) "
                    f"ON CONFLICT DO NOTHING"
                )
                await conn.execute(sql, *values)

    async def bulk_upsert_records(
        self,
        table_name: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Bulk INSERT ... ON CONFLICT DO NOTHING for junction/record tables."""
        if not items:
            return
        cols = self._table_columns(table_name)
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for item in items:
                table_data = {k: v for k, v in item.items() if k in cols}
                if not table_data:
                    continue
                col_names = [self._q(k) for k in table_data]
                values = list(table_data.values())
                placeholders = [f"${i + 1}" for i in range(len(values))]
                sql = (
                    f"INSERT INTO {table_name} ({', '.join(col_names)}) "
                    f"VALUES ({', '.join(placeholders)}) "
                    f"ON CONFLICT DO NOTHING"
                )
                await conn.execute(sql, *values)

    # ── Preload ──────────────────────────────────────────────────────

    async def preload(
        self,
        model_types: list[type[FanslyObject]],
        batch_size: int = 500,
    ) -> None:
        """Bulk load DB -> local cache.

        Order matters: load simple models BEFORE hub entities so
        relationship resolution works during construction.

        Rows are fetched via a server-side cursor in batches of
        *batch_size* (default 500) to provide progress feedback and
        reduce peak memory usage for large tables.
        """
        progress = get_progress_manager()

        with progress.session():
            models_task = progress.add_task(
                name="preload_models",
                description="Preloading models",
                total=len(model_types),
                show_elapsed=True,
            )

            for model_type in model_types:
                db_logger.info(f"Preloading {model_type.__name__}...")

                # Pre-fetch all M2M associations for this type
                assoc_data = await self._fetch_all_associations(model_type)

                table_name = model_type.__table_name__
                pool = await self._get_pool()

                # Get total row count for the per-model progress bar
                total_rows = await pool.fetchval(f"SELECT COUNT(*) FROM {table_name}")

                row_task = progress.add_task(
                    name="preload_rows",
                    description=f"{model_type.__name__}",
                    total=total_rows or 0,
                    parent_task="preload_models",
                    show_elapsed=True,
                )

                # Stream rows in batches via server-side cursor
                async with pool.acquire() as conn, conn.transaction(readonly=True):
                    cursor = await conn.cursor(f"SELECT * FROM {table_name}")
                    while True:
                        batch = await cursor.fetch(batch_size)
                        if not batch:
                            break
                        for row in batch:
                            data = dict(row)
                            eid = data.get("id") or data.get(
                                self._pk_column(model_type)
                            )
                            if eid and eid in assoc_data:
                                data.update(assoc_data[eid])
                            data = self._prepare_row_data(model_type, data)
                            obj = model_type.model_validate(data)
                            obj._is_new = False  # loaded from DB, not new
                        progress.update_task(row_task, advance=len(batch))

                progress.remove_task(row_task)
                self._fully_loaded.add(model_type)
                count = sum(1 for (t, _) in self._cache if t is model_type)
                db_logger.info(f"  {model_type.__name__}: {count} entities loaded")
                progress.update_task(models_task, advance=1)

    async def _fetch_all_associations(
        self, model_type: type[FanslyObject]
    ) -> dict[int, dict[str, Any]]:
        """Fetch all M2M data: {entity_id: {"field": [id1, ...] or id, ...}}

        For is_list=True relationships, returns list of IDs.
        For is_list=False relationships (e.g., avatar/banner), unwraps to single ID.
        """
        result: dict[int, dict[str, Any]] = defaultdict(dict)

        for field_name, meta in model_type.__relationships__.items():
            if not meta.assoc_table:
                continue
            assoc_def = core_metadata.tables.get(meta.assoc_table)
            if assoc_def is None:  # pragma: no cover
                continue
            col_names = [c.name for c in assoc_def.columns]
            if meta.fk_column and meta.fk_column in col_names:
                owner_fk = meta.fk_column
                related_fk = next(c for c in col_names if c != owner_fk)
            else:
                owner_fk, related_fk = col_names[0], col_names[1]

            pool = await self._get_pool()
            rows = await pool.fetch(
                f"SELECT {self._q(owner_fk)}, {self._q(related_fk)} "
                f"FROM {meta.assoc_table}"
            )
            for row in rows:
                oid, rid = row[owner_fk], row[related_fk]
                if meta.is_list:
                    result[oid].setdefault(field_name, []).append(rid)
                else:
                    # Scalar relationship (e.g., avatar, banner) — single ID
                    result[oid][field_name] = rid

        return result

    # ── Query Builders ───────────────────────────────────────────────

    async def _fetch_one_by_id(
        self, model_type: type[FanslyObject], entity_id: int
    ) -> asyncpg.Record | None:
        pk_col = self._pk_column(model_type)
        table_name = model_type.__table_name__
        sql = f"SELECT * FROM {table_name} WHERE {self._q(pk_col)} = $1"
        pool = await self._get_pool()
        return await pool.fetchrow(sql, entity_id)

    def _validate_order_by(
        self, model_type: type[FanslyObject], sort_spec: OrderBySpec
    ) -> None:
        """Validate order_by columns against model's table columns."""
        valid_cols = self._table_columns(model_type.__table_name__) | {"id"}
        for col, _direction in sort_spec:
            if col not in valid_cols:
                raise ValueError(
                    f"Invalid order_by column {col!r} for "
                    f"{model_type.__table_name__}. "
                    f"Valid columns: {sorted(valid_cols)}"
                )

    def _build_where_clauses(
        self,
        table_name: str,
        parsed_filters: list[tuple[str, str, Any]],
        start_idx: int = 1,
    ) -> tuple[list[str], list[Any], int]:
        """Build WHERE clause parts from parsed filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = start_idx

        for field, lookup, value in parsed_filters:
            col = f"{table_name}.{self._q(field)}"

            if lookup == "null":
                conditions.append(f"{col} IS NULL" if value else f"{col} IS NOT NULL")
            elif lookup in ("contains", "icontains"):
                conditions.append(f"{col} ILIKE ${idx}")
                params.append(f"%{value}%")
                idx += 1
            elif lookup == "iexact":
                conditions.append(f"LOWER({col}) = LOWER(${idx})")
                params.append(value)
                idx += 1
            elif lookup == "in":
                conditions.append(f"{col} = ANY(${idx})")
                params.append(list(value) if not isinstance(value, list) else value)
                idx += 1
            elif lookup == "between":
                conditions.append(f"{col} BETWEEN ${idx} AND ${idx + 1}")
                params.extend([value[0], value[1]])
                idx += 2
            else:
                op = _SQL_OPS.get(lookup, "=")
                conditions.append(f"{col} {op} ${idx}")
                params.append(value)
                idx += 1

        return conditions, params, idx

    async def _query_with_filters(
        self,
        model_type: type[FanslyObject],
        parsed_filters: list[tuple[str, str, Any]],
        limit: int | None = None,
        offset: int | None = None,
        order_by: OrderBySpec | None = None,
    ) -> list[asyncpg.Record]:
        """Translate parsed filters to SQL WHERE and execute."""
        table_name = model_type.__table_name__
        sql = f"SELECT * FROM {table_name}"

        conditions, params, _idx = self._build_where_clauses(table_name, parsed_filters)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        if order_by:
            # Column validation already done by caller (_validate_order_by)
            clauses = [
                f"{self._q(col)} {direction.value}" for col, direction in order_by
            ]
            sql += " ORDER BY " + ", ".join(clauses)

        if limit is not None:
            sql += f" LIMIT {limit}"
        if offset is not None:
            sql += f" OFFSET {offset}"

        pool = await self._get_pool()
        return await pool.fetch(sql, *params)

    @staticmethod
    def _sort_results(results: list[T], sort_spec: OrderBySpec) -> list[T]:
        """Sort results in Python (cache path). Applies multi-column sort.

        Mirrors SQL ORDER BY behavior: first column is primary sort,
        subsequent columns break ties. None values sort last (matching
        PostgreSQL NULLS LAST default for ASC).
        """
        if not sort_spec or not results:
            return results

        # Python's sort is stable, so apply in reverse order for multi-column
        for col, direction in reversed(sort_spec):
            reverse = direction is SortDirection.DESC
            results = sorted(
                results,
                key=lambda obj, c=col: (
                    # None-safe: sort None values last (ASC) or first (DESC)
                    (0, getattr(obj, c, None))
                    if getattr(obj, c, None) is not None
                    else (1, None)
                ),
                reverse=reverse,
            )
        return results

    @staticmethod
    def _prepare_row_data(
        model_type: type[FanslyObject], data: dict[str, Any]
    ) -> dict[str, Any]:
        """Inject FK values as relationship fields for cache resolution.

        Only injects for list relationships (M2M / reverse FK) where the
        field expects a list of IDs. Scalar belongs_to relationships are
        NOT injected here — _process_nested_cache_lookups resolves them
        from the FK column directly, avoiding raw ints in typed fields.
        """
        for field_name, meta in model_type.__relationships__.items():
            if meta.is_list and meta.fk_column and meta.fk_column in data:
                data[field_name] = data[meta.fk_column]
        return data

    # ── Cache Management ─────────────────────────────────────────────

    def invalidate(self, model_type: type, entity_id: int) -> None:
        self._cache.pop((model_type, entity_id), None)

    def invalidate_type(self, model_type: type) -> None:
        for k in [k for k in self._cache if k[0] is model_type]:
            del self._cache[k]
        self._fully_loaded.discard(model_type)

    def invalidate_all(self) -> None:
        self._cache.clear()
        self._fully_loaded.clear()

    def cache_stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for model_type, _ in self._cache:
            by_type[model_type.__name__] += 1
        return {
            "total": len(self._cache),
            "by_type": dict(by_type),
            "fully_loaded": [t.__name__ for t in self._fully_loaded],
        }

    def get_stats(self) -> dict[str, int]:
        """Return a snapshot of tier hit/miss counters."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats.clear()

    async def close(self) -> None:
        """Close store: clean up thread pools, clear cache."""
        await self.close_thread_resources()
        self._cache.clear()
        self._fully_loaded.clear()
        FanslyObject._store = None
        db_logger.info("PostgresEntityStore closed")
