"""Tests for media batch chunking and routing logic.

Two distinct concerns under test:

* **Chunking** (tests 1-2): ``_process_media_batch_by_mimetype`` splits
  ``media_list`` into chunks of ``max_batch_size = 20`` before delegating
  to ``_process_batch_internal``. Verified by TrueSpy on the inner method.
* **Routing** (tests 3-5): ``_process_batch_internal`` groups media by
  ``stash_id`` vs path-based, then groups path media by image vs scene
  mimetype. Verified at the project's documented HTTP boundary via
  ``respx`` — the GraphQL operation that fires (``findImages`` vs
  ``findScenes``) is the routing signal; result-list contents prove
  the routing landed media in the correct group.
"""

from unittest.mock import patch

import httpx
import pytest
import respx
from stash_graphql_client.types import Studio

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_find_scenes_result,
    create_graphql_response,
    create_image_dict,
    create_scene_dict,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestBatchProcessing:
    """Test media batch processing methods."""

    @pytest.mark.asyncio
    async def test_process_media_batch_small(
        self, respx_stash_processor, mock_item, mock_account
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

        respx_stash_processor._studio = Studio(id="9999", name="test_user (Fansly)")

        # 5 items in 1 chunk → 1 findImages call. Empty result is fine; the
        # chunking math under test is observed via the spy.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        create_find_images_result(count=0, images=[]),
                    ),
                ),
            ]
        )

        captured_chunks = []
        original_process_batch = respx_stash_processor._process_batch_internal

        async def spy_process_batch(media_list, item, account):
            captured_chunks.append(
                {"size": len(media_list), "ids": [m.id for m in media_list]}
            )
            return await original_process_batch(media_list, item, account)

        try:
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
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_media_batch_small")

        assert len(captured_chunks) == 1
        assert captured_chunks[0]["size"] == 5
        assert "images" in result
        assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_media_batch_large(
        self, respx_stash_processor, mock_item, mock_account
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

        respx_stash_processor._studio = Studio(id="9999", name="test_user (Fansly)")

        # 45 items split into 3 outer chunks (20+20+5) → 3 findImages calls.
        empty_images = create_graphql_response(
            "findImages",
            create_find_images_result(count=0, images=[]),
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(200, json=empty_images),
                httpx.Response(200, json=empty_images),
                httpx.Response(200, json=empty_images),
            ]
        )

        captured_chunks = []
        original_process_batch = respx_stash_processor._process_batch_internal

        async def spy_process_batch(media_list, item, account):
            captured_chunks.append({"size": len(media_list)})
            return await original_process_batch(media_list, item, account)

        try:
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
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_media_batch_large")

        assert [c["size"] for c in captured_chunks] == [20, 20, 5]

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_stash_ids(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Image media with ``stash_id`` route through a single findImages call."""
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

        respx_stash_processor._studio = Studio(id="9999", name="test_user (Fansly)")

        organized_images = [
            create_image_dict(
                id=str(40000 + i),
                organized=True,
                visual_files=[
                    {
                        "__typename": "ImageFile",
                        "id": f"file-{i}",
                        "path": f"/stash/{40000 + i}.jpg",
                    }
                ],
            )
            for i in range(3)
        ]
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        create_find_images_result(count=3, images=organized_images),
                    ),
                ),
            ]
        )

        try:
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_process_batch_internal_with_stash_ids"
            )

        # Single GraphQL call → routing produced exactly one Image-type lookup.
        assert graphql_route.call_count == 1
        # Routing landed all 3 images in result["images"], none in result["scenes"].
        assert len(result["images"]) == 3
        assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_paths(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Image media without ``stash_id`` route through a path-based findImages call."""
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

        respx_stash_processor._studio = Studio(id="9999", name="test_user (Fansly)")

        organized_images = [
            create_image_dict(
                id=str(media.id),
                organized=True,
                visual_files=[
                    {
                        "__typename": "ImageFile",
                        "id": f"file-{media.id}",
                        "path": f"/stash/{media.id}.jpg",
                    }
                ],
            )
            for media in media_list
        ]
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        create_find_images_result(count=3, images=organized_images),
                    ),
                ),
            ]
        )

        try:
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_process_batch_internal_with_paths"
            )

        # Single findImages call → routing fired the Image-type path lookup once.
        assert graphql_route.call_count == 1
        assert len(result["images"]) == 3
        assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_mixed_mimetype(
        self, respx_stash_processor, mock_item, mock_account
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

        respx_stash_processor._studio = Studio(id="9999", name="test_user (Fansly)")

        organized_images = [
            create_image_dict(
                id=str(media_id),
                organized=True,
                visual_files=[
                    {
                        "__typename": "ImageFile",
                        "id": f"file-{media_id}",
                        "path": f"/stash/{media_id}.jpg",
                    }
                ],
            )
            for media_id in image_ids
        ]
        organized_scenes = [
            create_scene_dict(
                id=str(media_id),
                title=f"Scene {media_id}",
                organized=True,
                files=[
                    {
                        "__typename": "VideoFile",
                        "id": f"file-{media_id}",
                        "path": f"/stash/{media_id}.mp4",
                    }
                ],
            )
            for media_id in scene_ids
        ]
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        create_find_images_result(count=2, images=organized_images),
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findScenes",
                        create_find_scenes_result(count=2, scenes=organized_scenes),
                    ),
                ),
            ]
        )

        try:
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_process_batch_internal_mixed_mimetype"
            )

        # Two GraphQL calls — one for images, one for scenes — landing each in
        # the correct result group.
        assert graphql_route.call_count == 2
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
