"""Tests for media batch chunking and routing logic.

Two distinct concerns under test:

* **Chunking** (tests 1-2): ``_process_media_batch_by_mimetype`` splits
  ``media_list`` into chunks of ``max_batch_size = 20`` before delegating
  to ``_process_batch_internal``. Verified by TrueSpy on the inner method
  per the canonical pattern at
  ``tests/stash/processing/integration/test_message_processing.py:173-238``.
* **Routing** (tests 3-5): ``_process_batch_internal`` groups media by
  ``stash_id`` vs path-based, then groups path media by image vs scene
  mimetype. Verified by patching the EntityStore leaves
  (``store.get_many`` / ``store.find_iter``, both rule-compliant external
  lib leaves per CLAUDE.md) to inject organized fakes, which causes
  ``_update_stash_metadata`` to take its production short-circuit at
  ``stash/processing/mixins/media.py:472-486`` instead of issuing
  GraphQL update mutations.
"""

from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from stash_graphql_client.types import Image, Scene, Studio

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash.stash_type_factories import (
    ImageFileFactory,
    VideoFileFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


def _organized_image(stash_id: str | int, path: str | None = None) -> Image:
    """Build an Image with ``organized=True`` so the metadata update short-circuits."""
    return Image(
        id=str(stash_id),
        organized=True,
        visual_files=[ImageFileFactory(path=path or f"/stash/{stash_id}.jpg")],
    )


def _organized_scene(stash_id: str | int, path: str | None = None) -> Scene:
    """Build a Scene with ``organized=True`` so the metadata update short-circuits."""
    return Scene(
        id=str(stash_id),
        organized=True,
        files=[VideoFileFactory(path=path or f"/stash/{stash_id}.mp4")],
    )


def _async_iter(items: list) -> AsyncIterator:
    """Wrap a list as an async iterator (matches ``store.find_iter`` shape)."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


@pytest.fixture
def fake_studio():
    return Studio(id="9999", name="test_user (Fansly)")


class TestBatchProcessing:
    """Test media batch processing methods."""

    @pytest.mark.asyncio
    async def test_process_media_batch_small(
        self, respx_stash_processor, mock_item, mock_account, fake_studio, monkeypatch
    ):
        """``_process_media_batch_by_mimetype`` does not split when batch <= 20."""
        media_list = [
            MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=20000 + i,
            )
            for i in range(5)
        ]

        # Pre-set studio (production short-circuit; see _process_batch_internal:996-998).
        respx_stash_processor._studio = fake_studio
        # store.get_many is the external-lib leaf called by _find_stash_files_by_id;
        # returning [] is sufficient — the chunking math under test is upstream.
        monkeypatch.setattr(
            respx_stash_processor.store,
            "get_many",
            lambda *_args, **_kwargs: _empty_list(),
        )

        captured_chunks = []
        original_process_batch = respx_stash_processor._process_batch_internal

        async def spy_process_batch(media_list, item, account):
            captured_chunks.append(
                {"size": len(media_list), "ids": [m.id for m in media_list]}
            )
            return await original_process_batch(media_list, item, account)

        with patch.object(
            respx_stash_processor,
            "_process_batch_internal",
            side_effect=spy_process_batch,
        ):
            result = await respx_stash_processor._process_media_batch_by_mimetype(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

        # Single delegated call carrying all 5 items.
        assert len(captured_chunks) == 1
        assert captured_chunks[0]["size"] == 5
        assert "images" in result
        assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_media_batch_large(
        self, respx_stash_processor, mock_item, mock_account, fake_studio, monkeypatch
    ):
        """``_process_media_batch_by_mimetype`` splits 45 items into 20+20+5."""
        media_list = [
            MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=30000 + i,
            )
            for i in range(45)
        ]

        respx_stash_processor._studio = fake_studio
        monkeypatch.setattr(
            respx_stash_processor.store,
            "get_many",
            lambda *_args, **_kwargs: _empty_list(),
        )

        captured_chunks = []
        original_process_batch = respx_stash_processor._process_batch_internal

        async def spy_process_batch(media_list, item, account):
            captured_chunks.append({"size": len(media_list)})
            return await original_process_batch(media_list, item, account)

        with patch.object(
            respx_stash_processor,
            "_process_batch_internal",
            side_effect=spy_process_batch,
        ):
            await respx_stash_processor._process_media_batch_by_mimetype(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

        # Chunk sizes match max_batch_size=20 split of 45 items.
        assert [c["size"] for c in captured_chunks] == [20, 20, 5]

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_stash_ids(
        self, respx_stash_processor, mock_item, mock_account, fake_studio, monkeypatch
    ):
        """Media with ``stash_id`` route through ``store.get_many`` once per type."""
        media_list = [
            MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=40000 + i,
            )
            for i in range(3)
        ]

        respx_stash_processor._studio = fake_studio

        captured_get_many = []

        async def fake_get_many(entity_type, ids):
            captured_get_many.append(
                {"entity_type": entity_type.__name__, "ids": list(ids)}
            )
            return [_organized_image(stash_id=i) for i in ids]

        monkeypatch.setattr(respx_stash_processor.store, "get_many", fake_get_many)

        result = await respx_stash_processor._process_batch_internal(
            media_list=media_list,
            item=mock_item,
            account=mock_account,
        )

        # Single Image-typed lookup carrying all 3 stash_ids.
        assert len(captured_get_many) == 1
        assert captured_get_many[0]["entity_type"] == "Image"
        assert len(captured_get_many[0]["ids"]) == 3

        # All 3 organized Images flowed into result.
        assert len(result["images"]) == 3
        assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_paths(
        self, respx_stash_processor, mock_item, mock_account, fake_studio, monkeypatch
    ):
        """Media without ``stash_id`` route through ``store.find_iter`` for path lookup."""
        media_list = []
        for _ in range(3):
            media = MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                # No stash_id → path-based lookup.
            )
            media.variants = set()
            media_list.append(media)

        respx_stash_processor._studio = fake_studio

        captured_find_iter = []

        def fake_find_iter(entity_type, **filters):
            captured_find_iter.append(
                {"entity_type": entity_type.__name__, "filters": filters}
            )
            return _async_iter(
                [
                    _organized_image(
                        stash_id=str(media.id), path=f"/stash/{media.id}.jpg"
                    )
                    for media in media_list
                ]
            )

        monkeypatch.setattr(respx_stash_processor.store, "find_iter", fake_find_iter)

        result = await respx_stash_processor._process_batch_internal(
            media_list=media_list,
            item=mock_item,
            account=mock_account,
        )

        # find_iter called for Image lookup (path-regex fallback path).
        assert len(captured_find_iter) == 1
        assert captured_find_iter[0]["entity_type"] == "Image"

        assert len(result["images"]) == 3
        assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_mixed_mimetype(
        self, respx_stash_processor, mock_item, mock_account, fake_studio, monkeypatch
    ):
        """Mixed image+video media route to result['images'] and result['scenes']."""
        media_list = []
        for _ in range(2):
            media = MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
            )
            media.variants = set()
            media_list.append(media)
        for _ in range(2):
            media = MediaFactory.build(
                id=snowflake_id(),
                mimetype="video/mp4",
                is_downloaded=True,
                accountId=mock_account.id,
            )
            media.variants = set()
            media_list.append(media)

        image_ids = [m.id for m in media_list[:2]]
        scene_ids = [m.id for m in media_list[2:]]

        respx_stash_processor._studio = fake_studio

        def fake_find_iter(entity_type, **filters):
            if entity_type is Image:
                return _async_iter(
                    [
                        _organized_image(
                            stash_id=str(media_id), path=f"/stash/{media_id}.jpg"
                        )
                        for media_id in image_ids
                    ]
                )
            return _async_iter(
                [
                    _organized_scene(
                        stash_id=str(media_id), path=f"/stash/{media_id}.mp4"
                    )
                    for media_id in scene_ids
                ]
            )

        monkeypatch.setattr(respx_stash_processor.store, "find_iter", fake_find_iter)

        result = await respx_stash_processor._process_batch_internal(
            media_list=media_list,
            item=mock_item,
            account=mock_account,
        )

        # Routing: images and scenes land in their respective result lists.
        assert len(result["images"]) == 2
        assert len(result["scenes"]) == 2

    @pytest.mark.asyncio
    async def test_process_batch_internal_empty_list(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Empty media_list → early return with empty dict."""
        result = await respx_stash_processor._process_batch_internal(
            media_list=[],
            item=mock_item,
            account=mock_account,
        )

        assert result["images"] == []
        assert result["scenes"] == []


async def _empty_list():
    """Async helper returning an empty list (for ``store.get_many`` stand-in)."""
    return []
