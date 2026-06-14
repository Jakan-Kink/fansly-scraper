"""Cold-cache reconstruction of ``Post.mentions`` (has_many) — parallel to #15.

A cold preload (STASH_ONLY, or a daemon pass on a post that aged out of the
identity map) loads ``PostMention`` rows but never populates the
``Post.mentions`` reverse-FK list. ``_reconstruct_attachment_lists`` rebuilds
only ``attachments``, so without a matching mention reconstruction the
mention-linking code (``_setup_gallery_performers`` / ``_stamp_performers``)
runs against an empty list and silently links no mentioned performers.
"""

import pytest

from metadata import PostMention
from tests.fixtures.metadata.metadata_factories import AccountFactory, PostFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestReconstructMentionLists:
    """StashProcessing._reconstruct_mention_lists rebuilds the cold-empty list."""

    @pytest.mark.asyncio
    async def test_cold_cache_post_mentions_rebuilt_from_preloaded_rows(
        self, entity_store, respx_stash_processor
    ):
        """PostMention rows present + Post.mentions empty -> reconstruction fills it.

        Mirrors the cold-cache state the production preload leaves (rows loaded,
        reverse list empty). Discriminating: without the reconstruction the post
        stays mention-less and downstream performer linking is a silent no-op.
        """
        acct = AccountFactory.build(id=snowflake_id(), username="creator")
        await entity_store.save(acct)
        post = PostFactory.build(id=snowflake_id(), accountId=acct.id)
        await entity_store.save(post)

        await entity_store.save(PostMention(id=1, postId=post.id, handle="alice"))
        await entity_store.save(PostMention(id=2, postId=post.id, handle="bob"))

        # Force the cold-cache state: rows are in the store, but the has_many
        # reverse list is empty (what preload leaves; save-time sync may differ).
        object.__setattr__(post, "mentions", [])
        post._snapshot["mentions"] = []
        assert not post.mentions

        respx_stash_processor._reconstruct_mention_lists()

        assert [m.handle for m in post.mentions] == ["alice", "bob"]
        # Assigned without dirtying — mentions is excluded from DB writes.
        assert post.is_dirty() is False
