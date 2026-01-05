"""Tests for media processing methods in MediaProcessingMixin."""

import json

import httpx
import pytest
import respx

from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_find_performers_result,
    create_find_scenes_result,
    create_find_studios_result,
    create_graphql_response,
    create_image_dict,
    create_scene_dict,
    create_studio_dict,
)


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_media(self, respx_stash_processor):
        """Test _process_media method.

        Unit test using respx - tests the full flow of finding and updating Stash metadata.
        """
        # Create test data using factories
        account = AccountFactory.build(id=123, username="test_user")
        item = PostFactory.build(id=456, accountId=123)

        # Create Media with stash_id so _find_stash_files_by_id gets called
        media = MediaFactory.build(
            id=789,
            mimetype="image/jpeg",
            is_downloaded=True,
            accountId=account.id,
            stash_id="stash_456",
        )
        media.variants = set()

        # Expected GraphQL call sequence (will verify with debug):
        # 1: findImage (by stash_id from _find_stash_files_by_id)
        # 2-3: findPerformers x2 (by name, by alias - from _update_stash_metadata)
        # 4: findStudios for Fansly (network)
        # 5: findStudios for creator studio
        # 6: studioCreate
        # 7: imageUpdate

        # Response 1: findImage - return image with visual_files
        image_file = {
            "id": "file_123",
            "path": "/path/to/media_789.jpg",
            "basename": "media_789.jpg",
            "size": 1024,
            "width": 1920,
            "height": 1080,
            "parent_folder_id": None,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        image_result = create_image_dict(
            id="image_stash_456",
            title="Test Image",
            visual_files=[image_file],
        )

        # Response 2-3: findPerformers (not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 4: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 5: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 6: studioCreate
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 7: imageUpdate result
        updated_image = create_image_dict(
            id="image_stash_456",
            title="Test Image",
            visual_files=[image_file],
            studio=creator_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_name
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_alias
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("imageUpdate", updated_image)
                ),
            ]
        )

        # Create empty result object
        result = {"images": [], "scenes": []}

        # Call method
        await respx_stash_processor._process_media(
            media=media,
            item=item,
            account=account,
            result=result,
        )

        # Verify result contains the image
        assert len(result["images"]) == 1
        assert result["images"][0].id == "image_stash_456"
        assert len(result["scenes"]) == 0

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        assert len(graphql_route.calls) == 7, "Expected exactly 7 GraphQL calls"

        calls = graphql_route.calls
        # Call 0: findImage (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findImage" in req0["query"]
        assert req0["variables"]["id"] == "stash_456"
        resp0 = calls[0].response.json()
        assert "findImage" in resp0["data"]

        # Call 1: findPerformers (by name)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert (
            req1["variables"]["performer_filter"]["name"]["value"] == account.username
        )
        assert req1["variables"]["performer_filter"]["name"]["modifier"] == "EQUALS"
        resp1 = calls[1].response.json()
        assert resp1["data"]["findPerformers"]["count"] == 0

        # Call 2: findPerformers (by alias)
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        assert (
            account.username
            in req2["variables"]["performer_filter"]["aliases"]["value"]
        )
        assert (
            req2["variables"]["performer_filter"]["aliases"]["modifier"] == "INCLUDES"
        )
        resp2 = calls[2].response.json()
        assert resp2["data"]["findPerformers"]["count"] == 0

        # Call 3: findStudios (Fansly network)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert "studio_filter" in req3["variables"]
        resp3 = calls[3].response.json()
        assert resp3["data"]["findStudios"]["count"] == 1

        # Call 4: findStudios (creator studio)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        assert "studio_filter" in req4["variables"]
        resp4 = calls[4].response.json()
        assert resp4["data"]["findStudios"]["count"] == 0

        # Call 5: studioCreate
        req5 = json.loads(calls[5].request.content)
        assert "studioCreate" in req5["query"]
        assert req5["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp5 = calls[5].response.json()
        assert resp5["data"]["studioCreate"]["id"] == "studio_123"

        # Call 6: imageUpdate
        req6 = json.loads(calls[6].request.content)
        assert "imageUpdate" in req6["query"]
        assert req6["variables"]["input"]["id"] == "image_stash_456"
        resp6 = calls[6].response.json()
        assert resp6["data"]["imageUpdate"]["id"] == "image_stash_456"

    @pytest.mark.asyncio
    async def test_process_media_with_stash_id(self, respx_stash_processor):
        """Test _process_media method with stash_id.

        Unit test using respx - verifies stash_id lookup path.
        """
        # Create test data using factories
        account = AccountFactory.build(id=123, username="test_user")
        item = PostFactory.build(id=456, accountId=123)

        # Create Media with stash_id
        media = MediaFactory.build(
            id=789,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
            stash_id="stash_123",
        )
        media.variants = set()

        # Expected GraphQL call sequence (will verify with debug):
        # 1: findScene (by stash_id)
        # 2-3: findPerformers x2 (by name, by alias)
        # 4: findStudios for Fansly (network)
        # 5: findStudios for creator studio
        # 6: studioCreate
        # 7: sceneUpdate

        # Response 1: findScene - return scene with files
        video_file = {
            "id": "file_456",
            "path": "/path/to/media_789.mp4",
            "basename": "media_789.mp4",
            "size": 2048,
            "parent_folder_id": None,
            "format": "mp4",
            "width": 1920,
            "height": 1080,
            "duration": 120.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "frame_rate": 30.0,
            "bit_rate": 5000000,
        }
        scene_result = create_scene_dict(
            id="scene_stash_123",
            title="Test Scene",
            files=[video_file],
        )

        # Response 2-3: findPerformers (not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 4: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 5: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 6: studioCreate
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 7: sceneUpdate result
        updated_scene = create_scene_dict(
            id="scene_stash_123",
            title="Test Scene",
            files=[video_file],
            studio=creator_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_name
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_alias
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("sceneUpdate", updated_scene)
                ),
            ]
        )

        # Create empty result object
        result = {"images": [], "scenes": []}

        # Call method
        await respx_stash_processor._process_media(
            media=media,
            item=item,
            account=account,
            result=result,
        )

        # Verify result contains the scene
        assert len(result["scenes"]) == 1
        assert result["scenes"][0].id == "scene_stash_123"
        assert len(result["images"]) == 0

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        assert len(graphql_route.calls) == 7, "Expected exactly 7 GraphQL calls"

        calls = graphql_route.calls
        # Call 0: findScene (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findScene" in req0["query"]
        assert req0["variables"]["id"] == "stash_123"
        resp0 = calls[0].response.json()
        assert "findScene" in resp0["data"]

        # Call 1: findPerformers (by name)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert (
            req1["variables"]["performer_filter"]["name"]["value"] == account.username
        )
        assert req1["variables"]["performer_filter"]["name"]["modifier"] == "EQUALS"
        resp1 = calls[1].response.json()
        assert resp1["data"]["findPerformers"]["count"] == 0

        # Call 2: findPerformers (by alias)
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        assert (
            account.username
            in req2["variables"]["performer_filter"]["aliases"]["value"]
        )
        assert (
            req2["variables"]["performer_filter"]["aliases"]["modifier"] == "INCLUDES"
        )
        resp2 = calls[2].response.json()
        assert resp2["data"]["findPerformers"]["count"] == 0

        # Call 3: findStudios (Fansly network)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert "studio_filter" in req3["variables"]
        resp3 = calls[3].response.json()
        assert resp3["data"]["findStudios"]["count"] == 1

        # Call 4: findStudios (creator studio)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        assert "studio_filter" in req4["variables"]
        resp4 = calls[4].response.json()
        assert resp4["data"]["findStudios"]["count"] == 0

        # Call 5: studioCreate
        req5 = json.loads(calls[5].request.content)
        assert "studioCreate" in req5["query"]
        assert req5["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp5 = calls[5].response.json()
        assert resp5["data"]["studioCreate"]["id"] == "studio_123"

        # Call 6: sceneUpdate (not imageUpdate - this is a video)
        req6 = json.loads(calls[6].request.content)
        assert "sceneUpdate" in req6["query"]
        assert req6["variables"]["input"]["id"] == "scene_stash_123"
        resp6 = calls[6].response.json()
        assert resp6["data"]["sceneUpdate"]["id"] == "scene_stash_123"

    @pytest.mark.asyncio
    async def test_process_media_with_variants(self, respx_stash_processor):
        """Test _process_media method with variants.

        Unit test using respx - verifies path-based lookup includes parent + variant IDs.
        """
        # Create test data using factories
        account = AccountFactory.build(id=123, username="test_user")
        item = PostFactory.build(id=456, accountId=123)

        # Create variant Media objects
        variant1 = MediaFactory.build(
            id="variant_1",
            mimetype="image/jpeg",
            accountId=account.id,
        )
        variant2 = MediaFactory.build(
            id="variant_2",
            mimetype="video/mp4",
            accountId=account.id,
        )
        variants = {variant1, variant2}

        # Create parent Media with variants (NO stash_id, so path lookup)
        media = MediaFactory.build(
            id=789,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
        )
        media.variants = variants

        # Expected GraphQL call sequence (verified with debug):
        # 0-1: findImages + findScenes (path-based file lookup)
        # 2-7: FIRST object (image): findPerformers x2, findStudios x2, studioCreate, imageUpdate
        # 8-11: SECOND object (scene): findStudios x2, studioCreate, sceneUpdate
        #       (NO findPerformers - cached via @async_lru_cache in StashClient)

        # Response 1: findImages - return images matching path filter (variant1)
        image_file = {
            "__typename": "ImageFile",
            "id": "file_variant_1",
            "path": "/path/to/media_variant_1.jpg",
            "basename": "media_variant_1.jpg",
            "size": 1024,
            "width": 1920,
            "height": 1080,
            "format": "jpg",
            "parent_folder_id": None,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        image_result = create_image_dict(
            id="image_variant_1",
            title="Test Image Variant",
            visual_files=[image_file],
        )
        images_result = create_find_images_result(
            count=1,
            images=[image_result],
            megapixels=2.07,
            filesize=1024,
        )

        # Response 2: findScenes - return scenes matching path filter (variant2 + parent)
        video_file = {
            "__typename": "VideoFile",
            "id": "file_variant_2",
            "path": "/path/to/media_variant_2.mp4",
            "basename": "media_variant_2.mp4",
            "size": 2048,
            "parent_folder_id": None,
            "format": "mp4",
            "width": 1920,
            "height": 1080,
            "duration": 120.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "frame_rate": 30.0,
            "bit_rate": 5000000,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        scene_result = create_scene_dict(
            id="scene_variant_2",
            title="Test Scene Variant",
            files=[video_file],
        )
        scenes_result = create_find_scenes_result(
            count=1,
            scenes=[scene_result],
            duration=120.0,
            filesize=2048,
        )

        # Response 3-4: findPerformers (not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 4: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 5: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 6: studioCreate
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 3-8: FIRST object (image) metadata updates
        updated_image = create_image_dict(
            id="image_variant_1",
            title="Test Image Variant",
            visual_files=[image_file],
            studio=creator_studio,
        )

        # Response 9-14: SECOND object (scene) metadata updates
        updated_scene = create_scene_dict(
            id="scene_variant_2",
            title="Test Scene Variant",
            files=[video_file],
            studio=creator_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Calls 0-1: Find files by path
                httpx.Response(
                    200, json=create_graphql_response("findImages", images_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findScenesByPathRegex", scenes_result
                    ),
                ),
                # Calls 2-7: Update FIRST object (image)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_name
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_alias
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("imageUpdate", updated_image)
                ),
                # Calls 8-13: Update SECOND object (scene)
                # findPerformers calls (not cached as expected)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_name
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_alias
                    ),
                ),
                # Studio processing
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("sceneUpdate", updated_scene)
                ),
            ]
        )

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call method
            await respx_stash_processor._process_media(
                media=media,
                item=item,
                account=account,
                result=result,
            )
        finally:
            print("GraphQL Call Debug Info:")
            for call_id, call in enumerate(graphql_route.calls):
                print(f"Call {call_id}:")
                print("Request:", call.request.content)
                print("Response:", call.response.json())
            print("End of GraphQL Call Debug Info")

        # Verify result contains BOTH image and scene
        assert len(result["images"]) == 1
        assert result["images"][0].id == "image_variant_1"
        assert len(result["scenes"]) == 1
        assert result["scenes"][0].id == "scene_variant_2"

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        # v0.7.x: Performer lookups repeated for each object, studio lookups cached
        # Sequence: 2 finds + 2 performers (img) + 2 studios + 1 create + 1 update (img) +
        #           2 performers (scene) + 2 studios + 1 create + 1 update (scene) = 14 calls
        assert len(graphql_route.calls) == 14, (
            f"Expected 14 calls, got {len(graphql_route.calls)}"
        )

        calls = graphql_route.calls
        # Call 0: findImages (path-based lookup)
        req0 = json.loads(calls[0].request.content)
        assert "findImages" in req0["query"]
        assert "image_filter" in req0["variables"]
        resp0 = calls[0].response.json()
        assert resp0["data"]["findImages"]["count"] == 1

        # Call 1: findScenesByPathRegex (path-based lookup) - v0.7.x uses filter.q instead of scene_filter
        req1 = json.loads(calls[1].request.content)
        assert "findScenesByPathRegex" in req1["query"]
        assert "filter" in req1["variables"]
        assert "q" in req1["variables"]["filter"]
        resp1 = calls[1].response.json()
        assert resp1["data"]["findScenesByPathRegex"]["count"] == 1

        # Calls 2-7: FIRST object (image) metadata updates
        # Call 2: findPerformers (by name) - FIRST TIME
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        assert (
            req2["variables"]["performer_filter"]["name"]["value"] == account.username
        )
        assert req2["variables"]["performer_filter"]["name"]["modifier"] == "EQUALS"
        resp2 = calls[2].response.json()
        assert resp2["data"]["findPerformers"]["count"] == 0

        # Call 3: findPerformers (by alias) - FIRST TIME
        req3 = json.loads(calls[3].request.content)
        assert "findPerformers" in req3["query"]
        assert (
            account.username
            in req3["variables"]["performer_filter"]["aliases"]["value"]
        )
        assert (
            req3["variables"]["performer_filter"]["aliases"]["modifier"] == "INCLUDES"
        )
        resp3 = calls[3].response.json()
        assert resp3["data"]["findPerformers"]["count"] == 0

        # Call 4: findStudios (Fansly network)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        assert "studio_filter" in req4["variables"]
        resp4 = calls[4].response.json()
        assert resp4["data"]["findStudios"]["count"] == 1

        # Call 5: findStudios (creator studio)
        req5 = json.loads(calls[5].request.content)
        assert "findStudios" in req5["query"]
        assert "studio_filter" in req5["variables"]
        resp5 = calls[5].response.json()
        assert resp5["data"]["findStudios"]["count"] == 0

        # Call 6: studioCreate
        req6 = json.loads(calls[6].request.content)
        assert "studioCreate" in req6["query"]
        assert req6["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp6 = calls[6].response.json()
        assert resp6["data"]["studioCreate"]["id"] == "studio_123"

        # Call 7: imageUpdate
        req7 = json.loads(calls[7].request.content)
        assert "imageUpdate" in req7["query"]
        assert req7["variables"]["input"]["id"] == "image_variant_1"
        resp7 = calls[7].response.json()
        assert resp7["data"]["imageUpdate"]["id"] == "image_variant_1"

        # Calls 8-13: SECOND object (scene) metadata updates
        # v0.7.x: Performer lookups repeated, studio lookups cached

        # Call 8: findPerformers (by name) - SECOND OBJECT
        req8 = json.loads(calls[8].request.content)
        assert "findPerformers" in req8["query"]

        # Call 9: findPerformers (by alias) - SECOND OBJECT
        req9 = json.loads(calls[9].request.content)
        assert "findPerformers" in req9["query"]

        # Call 10: findStudios (Fansly network) - SECOND OBJECT
        req10 = json.loads(calls[10].request.content)
        assert "findStudios" in req10["query"]

        # Call 11: findStudios (creator studio) - SECOND OBJECT
        req11 = json.loads(calls[11].request.content)
        assert "findStudios" in req11["query"]

        # Call 12: studioCreate - SECOND OBJECT
        req12 = json.loads(calls[12].request.content)
        assert "studioCreate" in req12["query"]

        # Call 13: sceneUpdate - SECOND OBJECT
        req13 = json.loads(calls[13].request.content)
        assert "sceneUpdate" in req13["query"]
        assert req13["variables"]["input"]["id"] == "scene_variant_2"
