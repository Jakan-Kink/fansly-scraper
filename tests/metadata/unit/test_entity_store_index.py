"""The store's field index: equality filters on an indexed field read only the
objects holding that value, and the index follows every way a field changes."""

import pytest

from metadata.models import Account, Media
from tests.fixtures.metadata.metadata_factories import AccountFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_field_index_narrows_searches_and_follows_changes(entity_store):
    """Searches by hash, file name and account use the index and stay correct.

    Covers the ways a field changes: plain assignment, a relationship
    assignment that syncs the foreign key, a merge through ``model_validate``,
    a delete, and dropping the whole type.
    """
    store = entity_store
    account_a = AccountFactory.build(id=snowflake_id(), username="index_a")
    account_b = AccountFactory.build(id=snowflake_id(), username="index_b")
    await store.save(account_a)
    await store.save(account_b)
    first, second, third = (
        MediaFactory.build(
            id=snowflake_id(),
            accountId=account_id,
            content_hash=content_hash,
            local_filename=name,
            is_downloaded=downloaded,
        )
        for account_id, content_hash, name, downloaded in (
            (account_a.id, "h1", "one.mp4", True),
            (account_a.id, "h1", "one.mp4", False),
            (account_b.id, None, None, False),
        )
    )
    for media in (first, second, third):
        await store.save(media)
    loaded = store.is_fully_loaded(Media)

    # Lookups: a shared value, a second filter on top, a miss, and each path.
    by_hash = {m.id for m in await store.find(Media, content_hash="h1")}
    narrowed = [
        m.id for m in await store.find(Media, content_hash="h1", is_downloaded=True)
    ]
    by_name = await store.find_one(Media, local_filename="one.mp4", is_downloaded=False)
    oldest = await store.find_one(Media, content_hash="h1", order_by="id")
    counted = await store.count(Media, accountId=account_a.id)
    missing = await store.find(Media, content_hash="no-such-hash")
    missing_one = await store.find_one(Media, local_filename="no-such-file")
    unhashable = await store.find(Media, content_hash=["h1"])
    batches = [
        [m.id for m in batch]
        async for batch in store.find_iter(Media, accountId=account_b.id)
    ]
    # Copied: the group is the live index entry and the steps below change it.
    checked = set(store._candidate_ids(Media, [("content_hash", "exact", "h1")]))
    smallest = store._candidate_ids(
        Media,
        [("accountId", "exact", account_a.id), ("local_filename", "exact", "x")],
    )
    smallest_first = store._candidate_ids(
        Media,
        [("local_filename", "exact", "x"), ("accountId", "exact", account_a.id)],
    )

    # Plain assignment moves the object between groups; both leave "h1".
    second.content_hash = "h2"
    after_assign = [m.id for m in await store.find(Media, content_hash="h1")]
    first.content_hash = None
    emptied = await store.find(Media, content_hash="h1")
    moved = [m.id for m in await store.find(Media, content_hash="h2")]
    # An object the store does not hold is never indexed.
    stray = Media.model_construct(id=snowflake_id(), accountId=account_a.id)
    stray.content_hash = "stray"
    unsaved = Media.model_construct(id=None, accountId=account_a.id)
    unsaved.content_hash = "stray"
    strays = await store.find(Media, content_hash="stray")
    # A relationship assignment syncs the foreign key, and the index with it.
    third.account = account_a
    after_relation = await store.count(Media, accountId=account_a.id)
    left_b = await store.find(Media, accountId=account_b.id)
    # A merge through model_validate rewrites fields on the cached object.
    merged = Media.model_validate({"id": first.id, "accountId": account_b.id})
    after_merge = [m.id for m in await store.find(Media, accountId=account_b.id)]
    # A delete and a dropped type leave nothing behind.
    await store.delete(second)
    after_delete = await store.find(Media, content_hash="h2")
    store.invalidate_type(Media)
    index_keys = [key for key in store._field_index if key[0] is Media]

    assert loaded is True
    assert by_hash == {first.id, second.id}
    assert narrowed == [first.id]
    assert by_name is second
    assert oldest is min((first, second), key=lambda m: m.id)
    assert counted == 2
    assert missing == []
    assert missing_one is None
    assert unhashable == []
    assert batches == [[third.id]]
    assert checked == {first.id, second.id}
    assert smallest == ()
    assert smallest_first == ()
    assert after_assign == [first.id]
    assert emptied == []
    assert moved == [second.id]
    assert strays == []
    assert after_relation == 3
    assert left_b == []
    assert merged is first
    assert after_merge == [first.id]
    assert after_delete == []
    assert index_keys == []
    assert isinstance(account_a, Account)
