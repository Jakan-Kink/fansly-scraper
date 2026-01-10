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

        # Expected GraphQL call sequence (verified with detailed logging):
        # After ORM migration + store.save() fix:
        # 1: findImage (by stash_id from _find_stash_files_by_id)
        # 2: findPerformers (by name only - from _find_existing_performer, no mentions in test)
        # 3: findStudios for Fansly (network)
        # 4: studioCreate (creates creator studio - returns full studio, no verification needed)
        # 5: imageUpdate
        # NOTE: is_preview=False so _add_preview_tag is not called

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

        # Response 2: findPerformers (not found - name only from _find_existing_performer)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: studioCreate (returns full studio - no verification needed)
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5: imageUpdate result
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
                # _find_existing_performer makes only 1 search (name only, no mentions)
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
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

        # Call method with detailed logging
        try:
            await respx_stash_processor._process_media(
                media=media,
                item=item,
                account=account,
                result=result,
            )
        finally:
            print("\n" + "=" * 80)
            print("DETAILED GRAPHQL CALL LOG")
            print("=" * 80)
            for index, call in enumerate(graphql_route.calls):
                print(f"\nCall {index}:")
                req_data = json.loads(call.request.content)
                # Extract query type from GraphQL query
                query_lines = req_data["query"].split("\n")
                query_type = "unknown"
                for query_line in query_lines:
                    line = query_line.strip()
                    if line.startswith(("query", "mutation")):
                        # Extract operation name
                        parts = line.split()
                        if len(parts) > 1:
                            query_type = parts[1].split("(")[0]
                        break
                print(f"  Request Type: {query_type}")
                print(
                    f"  Variables: {json.dumps(req_data.get('variables', {}), indent=4)}"
                )
                resp_data = call.response.json()
                print(f"  Response Keys: {list(resp_data.get('data', {}).keys())}")
                print(f"  Response: {json.dumps(resp_data, indent=4)[:500]}...")
            print("=" * 80 + "\n")

        # Verify result contains the image
        assert len(result["images"]) == 1
        assert result["images"][0].id == "image_stash_456"
        assert len(result["scenes"]) == 0

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        assert len(graphql_route.calls) == 5, (
            "Expected exactly 5 GraphQL calls (after store.save() fix)"
        )

        calls = graphql_route.calls
        # Call 0: findImage (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findImage" in req0["query"]
        assert req0["variables"]["id"] == "stash_456"
        resp0 = calls[0].response.json()
        assert "findImage" in resp0["data"]

        # Call 1: findPerformers (by name - from _find_existing_performer)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert (
            req1["variables"]["performer_filter"]["name"]["value"] == account.username
        )
        assert req1["variables"]["performer_filter"]["name"]["modifier"] == "EQUALS"
        resp1 = calls[1].response.json()
        assert resp1["data"]["findPerformers"]["count"] == 0

        # Call 2: findStudios (Fansly network)
        req2 = json.loads(calls[2].request.content)
        assert "findStudios" in req2["query"]
        assert "studio_filter" in req2["variables"]
        resp2 = calls[2].response.json()
        assert resp2["data"]["findStudios"]["count"] == 1

        # Call 3: studioCreate
        req3 = json.loads(calls[3].request.content)
        assert "studioCreate" in req3["query"]
        assert req3["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp3 = calls[3].response.json()
        assert resp3["data"]["studioCreate"]["id"] == "studio_123"

        # Call 4: imageUpdate
        req4 = json.loads(calls[4].request.content)
        assert "imageUpdate" in req4["query"]
        assert req4["variables"]["input"]["id"] == "image_stash_456"
        resp4 = calls[4].response.json()
        assert resp4["data"]["imageUpdate"]["id"] == "image_stash_456"

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

        # Expected GraphQL call sequence (verified with debug logging):
        # After ORM migration + store.save() fix:
        # 1: findScene (by stash_id from _find_stash_files_by_id)
        # 2: findPerformers (by name only - from _find_existing_performer, no mentions in test)
        # 3: findStudios for Fansly (network)
        # 4: studioCreate (creates creator studio - returns full studio, no verification needed)
        # 5: sceneUpdate

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

        # Response 2: findPerformers (not found)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: studioCreate
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5: sceneUpdate result
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
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
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
        assert len(graphql_route.calls) == 5, (
            "Expected exactly 5 GraphQL calls after ORM migration + store.save() fix"
        )

        calls = graphql_route.calls
        # Call 0: findScene (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findScene" in req0["query"]
        assert req0["variables"]["id"] == "stash_123"
        resp0 = calls[0].response.json()
        assert "findScene" in resp0["data"]

        # Call 1: findPerformers (by name only - ORM migration optimizes to single search)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert (
            req1["variables"]["performer_filter"]["name"]["value"] == account.username
        )
        assert req1["variables"]["performer_filter"]["name"]["modifier"] == "EQUALS"
        resp1 = calls[1].response.json()
        assert resp1["data"]["findPerformers"]["count"] == 0

        # Call 2: findStudios (Fansly network)
        req2 = json.loads(calls[2].request.content)
        assert "findStudios" in req2["query"]
        assert "studio_filter" in req2["variables"]
        resp2 = calls[2].response.json()
        assert resp2["data"]["findStudios"]["count"] == 1

        # Call 3: studioCreate (no verification search needed after store.save() fix)
        req3 = json.loads(calls[3].request.content)
        assert "studioCreate" in req3["query"]
        assert req3["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp3 = calls[3].response.json()
        assert resp3["data"]["studioCreate"]["id"] == "studio_123"

        # Call 4: sceneUpdate (not imageUpdate - this is a video)
        req4 = json.loads(calls[4].request.content)
        assert "sceneUpdate" in req4["query"]
        assert req4["variables"]["input"]["id"] == "scene_stash_123"
        resp4 = calls[4].response.json()
        assert resp4["data"]["sceneUpdate"]["id"] == "scene_stash_123"

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
        # After ORM migration + store.save() fix:
        # 0-1: findImages + findScenes (path-based file lookup - finds all 3 files)
        # Then EACH file is processed independently with full lookups:
        # FILE 1 (image variant):
        #   2: findPerformers
        #   3: findStudios for Fansly (network)
        #   4: studioCreate (creator studio - new)
        #   5: imageUpdate
        # FILE 2 (parent scene):
        #   6: findPerformers
        #   7: findStudios for Fansly (network)
        #   8: studioCreate (creator studio - tries to create again, returns existing)
        #   9: sceneUpdate
        # FILE 3 (scene variant):
        #   10: findPerformers
        #   11: findStudios for Fansly (network)
        #   12: studioCreate (creator studio - tries to create again, returns existing)
        #   13: sceneUpdate
        # Total: 14 calls (identity map doesn't prevent duplicate studio creation attempts)

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

        # Response 2: findScenes - return scenes matching path filter (parent + variant2)
        # Parent scene (ID=789)
        parent_video_file = {
            "__typename": "VideoFile",
            "id": "file_789",
            "path": "/path/to/media_789.mp4",
            "basename": "media_789.mp4",
            "size": 3072,
            "parent_folder_id": None,
            "format": "mp4",
            "width": 1920,
            "height": 1080,
            "duration": 180.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "frame_rate": 30.0,
            "bit_rate": 5000000,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        parent_scene_result = create_scene_dict(
            id="scene_789",
            title="Test Parent Scene",
            files=[parent_video_file],
        )

        # Variant scene (ID=variant_2)
        variant_video_file = {
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
        variant_scene_result = create_scene_dict(
            id="scene_variant_2",
            title="Test Scene Variant",
            files=[variant_video_file],
        )

        scenes_result = create_find_scenes_result(
            count=2,
            scenes=[parent_scene_result, variant_scene_result],
            duration=300.0,  # Combined duration
            filesize=5120,  # Combined filesize
        )

        # Response 3: findPerformers (not found)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 4: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 5, 8, 12: studioCreate (FILE 1 creates, FILES 2-3 try to create again)
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 6: imageUpdate (image variant)
        updated_image = create_image_dict(
            id="image_variant_1",
            title="Test Image Variant",
            visual_files=[image_file],
            studio=creator_studio,
        )

        # Response 7: sceneUpdate (parent scene)
        updated_parent_scene = create_scene_dict(
            id="scene_789",
            title="Test Parent Scene",
            files=[parent_video_file],
            studio=creator_studio,
        )

        # Response 8: sceneUpdate (scene variant)
        updated_variant_scene = create_scene_dict(
            id="scene_variant_2",
            title="Test Scene Variant",
            files=[variant_video_file],
            studio=creator_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Calls 0-1: Find files by path
                httpx.Response(
                    200, json=create_graphql_response("findImages", images_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findScenes", scenes_result)
                ),
                # FILE 1 (image variant)
                # Call 2: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 3: findStudios (Fansly network)
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # Call 4: studioCreate (creator studio)
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # Call 5: imageUpdate
                httpx.Response(
                    200, json=create_graphql_response("imageUpdate", updated_image)
                ),
                # FILE 2 (parent scene)
                # Call 6: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 7: findStudios (Fansly network)
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # Call 8: studioCreate (tries to create again, returns existing)
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # Call 9: sceneUpdate (parent scene)
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", updated_parent_scene),
                ),
                # FILE 3 (scene variant)
                # Call 10: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 11: findStudios (Fansly network)
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # Call 12: studioCreate (tries to create again, returns existing)
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # Call 13: sceneUpdate (scene variant)
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", updated_variant_scene),
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

        # Verify result contains image and BOTH scenes (parent + variant)
        assert len(result["images"]) == 1
        assert result["images"][0].id == "image_variant_1"
        assert len(result["scenes"]) == 2
        scene_ids = {s.id for s in result["scenes"]}
        assert "scene_789" in scene_ids, "Parent scene should be in result"
        assert "scene_variant_2" in scene_ids, "Variant scene should be in result"

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        # After ORM migration + store.save() fix:
        # Each file processes independently with full performer/studio lookups
        # Sequence: 2 path finds +
        #   FILE1: (performer + fansly + studioCreate + imageUpdate) +
        #   FILE2: (performer + fansly + studioCreate + sceneUpdate) +
        #   FILE3: (performer + fansly + studioCreate + sceneUpdate) = 14 calls
        # Note: Identity map doesn't prevent duplicate studioCreate attempts
        assert len(graphql_route.calls) == 14, (
            f"Expected 14 calls after ORM migration + store.save() fix, got {len(graphql_route.calls)}"
        )

        calls = graphql_route.calls
        # Call 0: findImages (path-based lookup)
        req0 = json.loads(calls[0].request.content)
        assert "findImages" in req0["query"]
        assert "image_filter" in req0["variables"]
        resp0 = calls[0].response.json()
        assert resp0["data"]["findImages"]["count"] == 1

        # Call 1: findScenes (path-based lookup with regex - returns parent + variant scene)
        req1 = json.loads(calls[1].request.content)
        assert "findScenes" in req1["query"]
        assert "scene_filter" in req1["variables"]
        resp1 = calls[1].response.json()
        assert resp1["data"]["findScenes"]["count"] == 2

        # FILE 1 (image variant) - Calls 2-5
        # Call 2: findPerformers
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        resp2 = calls[2].response.json()
        assert resp2["data"]["findPerformers"]["count"] == 0

        # Call 3: findStudios (Fansly network)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        resp3 = calls[3].response.json()
        assert resp3["data"]["findStudios"]["count"] == 1

        # Call 4: studioCreate
        req4 = json.loads(calls[4].request.content)
        assert "studioCreate" in req4["query"]
        resp4 = calls[4].response.json()
        assert resp4["data"]["studioCreate"]["id"] == "studio_123"

        # Call 5: imageUpdate
        req5 = json.loads(calls[5].request.content)
        assert "imageUpdate" in req5["query"]
        assert req5["variables"]["input"]["id"] == "image_variant_1"

        # FILE 2 (parent scene) - Calls 6-9
        # Call 6: findPerformers
        req6 = json.loads(calls[6].request.content)
        assert "findPerformers" in req6["query"]

        # Call 7: findStudios (Fansly network)
        req7 = json.loads(calls[7].request.content)
        assert "findStudios" in req7["query"]

        # Call 8: studioCreate (tries to create again, returns existing)
        req8 = json.loads(calls[8].request.content)
        assert "studioCreate" in req8["query"]
        resp8 = calls[8].response.json()
        assert resp8["data"]["studioCreate"]["id"] == "studio_123"

        # Call 9: sceneUpdate (parent scene)
        req9 = json.loads(calls[9].request.content)
        assert "sceneUpdate" in req9["query"]
        assert req9["variables"]["input"]["id"] == "scene_789"

        # FILE 3 (scene variant) - Calls 10-13
        # Call 10: findPerformers
        req10 = json.loads(calls[10].request.content)
        assert "findPerformers" in req10["query"]

        # Call 11: findStudios (Fansly network)
        req11 = json.loads(calls[11].request.content)
        assert "findStudios" in req11["query"]

        # Call 12: studioCreate (tries to create again, returns existing)
        req12 = json.loads(calls[12].request.content)
        assert "studioCreate" in req12["query"]
        resp12 = calls[12].response.json()
        assert resp12["data"]["studioCreate"]["id"] == "studio_123"

        # Call 13: sceneUpdate (scene variant)
        req13 = json.loads(calls[13].request.content)
        assert "sceneUpdate" in req13["query"]
        assert req13["variables"]["input"]["id"] == "scene_variant_2"
