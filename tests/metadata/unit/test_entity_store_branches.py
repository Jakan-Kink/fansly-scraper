"""Branch-coverage tests for PostgresEntityStore in-memory paths.

Targets arcs the existing suites miss: int-seconds default_ttl coercion (268),
the ``_is_expired`` no-timestamp short-circuit (441), the cache-hit autolink
link (495-496), and ``invalidate_type`` on an un-cached type (1632->1637).
These exercise real store methods directly — no DB round-trip is required for
the in-memory identity-map logic, so they reuse the real ``entity_store``
fixture's pool without issuing queries.
"""

from datetime import timedelta

import pytest
from stash_graphql_client.types.unset import UNSET

from metadata.entity_store import PostgresEntityStore
from metadata.models import Account, RelationshipMetadata
from tests.fixtures.metadata.metadata_factories import AccountFactory, PostFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class _AutolinkStub:
    """Minimal object exercising ``_autolink_relationships`` type-dispatch.

    ``belongs_to``/``has_*`` always pass ``inverse_type`` as a registry name
    string, so the ``isinstance(..., type)`` (486) and the non-type/non-str
    fallthrough (490) guards are only reachable with directly-built
    ``RelationshipMetadata``. Three relationships drive each arc:
      - ``by_type``  → ``inverse_type`` is the Account *class* (486),
      - ``by_int``   → neither type nor str → target_type None → continue (490, 492),
      - ``by_unreg`` → a str absent from the registry → None → continue (492).
    ``_snapshot`` is None so the cache-hit sync also takes 511->exit.
    """

    __relationships__ = {
        "by_type": RelationshipMetadata(
            "by_type", False, inverse_type=Account, fk_column="acc_id"
        ),
        "by_int": RelationshipMetadata(
            # inverse_type=None is in-contract yet still neither ``type`` nor
            # ``str``, so it drives the autolink else-branch (target_type None).
            "by_int",
            False,
            inverse_type=None,
            fk_column="acc_id",
        ),
        "by_unreg": RelationshipMetadata(
            "by_unreg", False, inverse_type="NoSuchType", fk_column="acc_id"
        ),
    }

    def __init__(self, acc_id: int) -> None:
        self._snapshot = None
        self.acc_id = acc_id
        self.by_type = UNSET
        self.by_int = UNSET
        self.by_unreg = UNSET


@pytest.mark.asyncio
async def test_constructor_coerces_int_default_ttl_to_timedelta(entity_store):
    """Line 268: an int default_ttl is converted to ``timedelta(seconds=...)``."""
    store = PostgresEntityStore(entity_store.pool, default_ttl=45)
    assert store._default_ttl == timedelta(seconds=45)


@pytest.mark.asyncio
async def test_constructor_keeps_timedelta_default_ttl(entity_store):
    """The non-int branch leaves a timedelta default_ttl untouched."""
    ttl = timedelta(minutes=5)
    store = PostgresEntityStore(entity_store.pool, default_ttl=ttl)
    assert store._default_ttl is ttl


@pytest.mark.asyncio
async def test_is_expired_false_when_no_cache_timestamp(entity_store):
    """Line 441: a TTL is set but the entry was never cached → not expired.

    ``_is_expired`` reads ``_cache_timestamps`` for the entry; with no recorded
    timestamp (entity never cached) it returns False rather than treating the
    missing entry as stale.
    """
    # Fresh store with a non-None default_ttl so the ``ttl is None`` guard at
    # 437 passes and control reaches the no-timestamp check at 440-441 (the
    # fixture store may carry a per-type ttl of None that would short-circuit).
    store = PostgresEntityStore(entity_store.pool, default_ttl=timedelta(seconds=60))
    account = AccountFactory.build(id=snowflake_id())
    # Never cached → no timestamp recorded for this key.
    assert store._is_expired(type(account), account.id) is False


@pytest.mark.asyncio
async def test_autolink_belongs_to_links_from_cache_hit(entity_store):
    """Lines 493-496: a cached parent is linked onto the child's UNSET relationship.

    The Post's ``account`` belongs_to (fk ``accountId``) resolves "Account" via
    the type registry; with that Account already in the identity map, autolink
    sets ``post.account`` to the cached instance.
    """
    account_id = snowflake_id()
    account = AccountFactory.build(id=account_id, username="autolink_parent")
    entity_store.cache_instance(account)

    post = PostFactory.build(id=snowflake_id(), accountId=account_id)
    # Force the relationship UNSET so autolink processes it (vs. an eager value).
    object.__setattr__(post, "account", UNSET)
    post.mark_clean()
    post._is_new = False

    entity_store._autolink_relationships(post)

    # Linked to the exact cached instance (identity map hit, not a copy).
    assert post.account is account


@pytest.mark.asyncio
async def test_invalidate_type_on_uncached_type_is_noop(entity_store):
    """Branch 1632->1637: ``invalidate_type`` for a type with no cached entries.

    ``_type_index.pop`` returns None, so the ``if ids:`` guard is False and the
    method skips straight to discarding the type from ``_fully_loaded`` without
    iterating any entries — and must not raise.
    """
    account = AccountFactory.build(id=snowflake_id())
    cls = type(account)
    # Ensure the type is genuinely absent from the index.
    entity_store._type_index.pop(cls, None)
    entity_store._fully_loaded.discard(cls)

    entity_store.invalidate_type(cls)  # must not raise

    assert cls not in entity_store._type_index
    assert cls not in entity_store._fully_loaded


@pytest.mark.asyncio
async def test_autolink_inverse_type_dispatch_and_snapshotless_sync(entity_store):
    """Lines 486, 490, 492 and branch 511->exit: every inverse_type shape.

    ``by_type`` resolves the Account *class* directly (486) and, with that
    Account cached, links it (495-496); since the stub has ``_snapshot is None``
    the snapshot sync takes 511->exit. ``by_int`` (non-type/non-str → 490 → 492)
    and ``by_unreg`` (registry miss → None → 492) both ``continue`` unlinked.
    """
    acc_id = snowflake_id()
    account = AccountFactory.build(id=acc_id, username="stub_parent")
    entity_store.cache_instance(account)

    stub = _AutolinkStub(acc_id)
    entity_store._autolink_relationships(stub)

    # by_type linked from the identity map; the unresolved kinds stay UNSET.
    assert stub.by_type is account
    assert stub.by_int is UNSET
    assert stub.by_unreg is UNSET
