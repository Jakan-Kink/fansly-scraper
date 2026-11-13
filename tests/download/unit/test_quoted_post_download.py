"""A timeline page with a quote post: whose media it is and where it is saved.

Driven by ``wrapped_aggregated_post.json``, cut down from a real post
response: the quoter's wrapper post points at the original post, which sits in
``aggregatedPosts`` under its own author together with that author's media.
"""

import pytest

from download.downloadstate import DownloadState
from download.media import _other_owner_name
from download.timeline import process_timeline_data
from download.types import DownloadType
from metadata import Media, Post
from metadata.models import get_store
from pathio import get_creator_base_path, get_media_save_path
from tests.fixtures import load_json_fixture
from tests.fixtures.metadata import MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


_QUOTER, _AUTHOR = 100000000000000001, 100000000000000002
_ORIGINAL, _WRAPPER = 300000000000000001, 300000000000000002
_MEDIA = 400000000000000001


@pytest.mark.parametrize(
    "author_folder_exists", [True, False], ids=["author-folder", "no-author-folder"]
)
@pytest.mark.asyncio
async def test_quoted_media_is_saved_under_its_original_poster(
    entity_store, mock_config, tmp_path, author_folder_exists
):
    """Quoted media belongs to the original poster and is filed under them.

    When the original poster has no folder on disk it stays under the quoter.
    The creator's own media, and media of an account the database has never
    seen, are filed under the creator as before.
    """
    config = mock_config
    config.download_directory = tmp_path
    page = load_json_fixture("json_data/wrapped_aggregated_post.json")["response"]
    state = DownloadState()
    state.creator_id = _QUOTER
    state.creator_name = "quoter"
    state.download_type = DownloadType.TIMELINE
    author_base = get_creator_base_path(config, "original_poster")
    if author_folder_exists:
        author_base.mkdir(parents=True)

    await process_timeline_data(config, state, page, 0)

    store = get_store()
    wrapper = store.get_from_cache(Post, _WRAPPER)
    original = store.get_from_cache(Post, _ORIGINAL)
    media = store.get_from_cache(Media, _MEDIA)
    assert wrapper is not None
    assert original is not None
    assert media is not None
    owner = await _other_owner_name(state, media)
    _save_dir, save_path = get_media_save_path(config, state, media, owner)
    own = await _other_owner_name(state, MediaFactory.build(accountId=_QUOTER))
    unknown = await _other_owner_name(
        state, MediaFactory.build(accountId=snowflake_id())
    )

    assert store is entity_store
    assert wrapper.accountId == _QUOTER
    assert [a.contentType.value for a in wrapper.attachments] == [8]
    assert wrapper.attachments[0].aggregated_post is original
    assert original.accountId == _AUTHOR
    assert media.accountId == _AUTHOR
    assert owner == "original_poster"
    assert (own, unknown) == (None, None)
    expected_base = (
        author_base if author_folder_exists else get_creator_base_path(config, "quoter")
    )
    assert save_path.parent == expected_base / "Timeline" / "Videos"
