"""Lock-discipline audit for PostgresEntityStore's identity-map RLock."""

import ast
import threading
from pathlib import Path

import pytest

import metadata.entity_store
from metadata.models import Account
from tests.fixtures.metadata import AccountFactory
from tests.fixtures.utils import (
    forbidden_nodes_in_lock_blocks,
    iter_lock_blocks,
    methods_containing_lock,
)


LOCK_ATTR = "_cache_lock"

# Every method that touches the guarded cluster (_cache, _type_index,
# _cache_timestamps, _fully_loaded, _col_cache) must acquire the lock.
LOCKED_METHODS = frozenset(
    {
        "get_from_cache",
        "cache_instance",
        "invalidate",
        "invalidate_type",
        "invalidate_all",
        "filter",
        "get",
        "find",
        "find_one",
        "count",
        "find_iter",
        "get_many",
        "_autolink_relationships",
        "preload",
        "_table_columns",
        "is_fully_loaded",
        "cache_stats",
        "close",
    }
)


def test_cache_lock_discipline():
    source = Path(metadata.entity_store.__file__).read_text()
    tree = ast.parse(source)

    blocks = iter_lock_blocks(tree, LOCK_ATTR)
    assert blocks, "no `with self._cache_lock:` blocks found in entity_store"

    violations = forbidden_nodes_in_lock_blocks(tree, LOCK_ATTR)
    assert violations == [], f"await/yield inside locked region: {violations}"

    locked = methods_containing_lock(tree, LOCK_ATTR)
    missing = LOCKED_METHODS - locked
    assert not missing, f"cluster-touching methods missing the lock: {missing}"


@pytest.mark.timeout(30)
async def test_cross_thread_cache_ops_smoke(entity_store):
    """Threads hammer the sync cache API concurrently.

    Under GIL Python this cannot prove the races away — it is a deadlock
    and re-entrancy canary (get_from_cache -> invalidate under RLock),
    plus a cross-structure consistency check afterwards.
    """
    accounts = [AccountFactory() for _ in range(100)]
    errors: list[BaseException] = []

    def hammer(worker: int) -> None:
        try:
            for i in range(300):
                acc = accounts[(worker * 300 + i) % len(accounts)]
                entity_store.cache_instance(acc)
                entity_store.get_from_cache(Account, acc.id)
                if i % 7 == 0:
                    entity_store.invalidate(Account, acc.id)
                if i % 13 == 0:
                    entity_store.filter(
                        Account, lambda a, acc_id=acc.id: a.id == acc_id
                    )
                if i % 29 == 0:
                    entity_store.cache_stats()
                    entity_store.is_fully_loaded(Account)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(w,), name=f"hammer-{w}") for w in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not any(t.is_alive() for t in threads), "deadlocked hammer thread"
    assert errors == []

    with entity_store._cache_lock:
        cache_keys = set(entity_store._cache)
        ts_keys = set(entity_store._cache_timestamps)
        index_keys = {
            (mt, eid) for mt, ids in entity_store._type_index.items() for eid in ids
        }
    assert cache_keys == ts_keys
    assert index_keys == cache_keys
