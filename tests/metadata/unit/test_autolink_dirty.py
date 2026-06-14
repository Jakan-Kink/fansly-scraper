"""Regression: autolink hydration must not mark a cold-loaded object dirty.

When a singular belongs_to is resolved from UNSET (a cold-preloaded Post whose
``inReplyTo`` FK is None), the autolink sets the field to None. That is
hydration, not a user mutation, so the dirty-tracking snapshot must stay in step
— otherwise the object reads ``is_dirty() == True`` and is needlessly re-saved.
"""

import pytest
from stash_graphql_client.types.unset import UNSET

from tests.fixtures.metadata.metadata_factories import PostFactory
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_autolink_unset_belongs_to_does_not_mark_dirty(entity_store):
    """Autolink UNSET -> None for a belongs_to leaves the object clean."""
    post = PostFactory.build(
        id=snowflake_id(),
        accountId=snowflake_id(),
        inReplyTo=None,
        inReplyToRoot=None,
    )
    # Fresh-from-DB state: the singular relationships are UNSET, the snapshot
    # matches (clean baseline), and the object is not new.
    object.__setattr__(post, "replyTo", UNSET)
    object.__setattr__(post, "replyToRoot", UNSET)
    post.mark_clean()
    post._is_new = False
    assert not post.is_dirty()

    entity_store._autolink_relationships(post)

    # Hydrated to None, but NOT dirty — the snapshot was synced.
    assert post.replyTo is None
    assert post.replyToRoot is None
    assert not post.is_dirty()
