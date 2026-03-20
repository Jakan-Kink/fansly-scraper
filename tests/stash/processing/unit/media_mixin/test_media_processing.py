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
from tests.fixtures.utils.test_isolation import snowflake_id


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_media(self, respx_stash_processor):
        """Test _process_media method.

        Unit test using respx - tests the full flow of finding and updating Stash metadata.
        """
        # Create test data using factories
        acct_id = snowflake_id()
        post_id = snowflake_id()
        media_id = snowflake_id()

        account = AccountFactory.build(id=acct_id, username="test_user")
        item = PostFactory.build(id=post_id, accountId=acct_id)

        # Create Media with stash_id so _find_stash_files_by_id gets called
        media = MediaFactory.build(
            id=media_id,
            mimetype="image/jpeg",
            is_downloaded=True,
            accountId=account.id,
            stash_id=456,
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
            "id": "2123",
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
            id="456",
            title="Test Image",
            visual_files=[image_file],
        )

        # Response 2: findPerformers (not found - name only from _find_existing_performer)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="1123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 6: imageUpdate result
        updated_image = create_image_dict(
            id="456",
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
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
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
        assert result["images"][0].id == "456"
        assert len(result["scenes"]) == 0

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        assert len(graphql_route.calls) == 6, (
            "Expected exactly 6 GraphQL calls (findImage + findPerformers + 2 findStudios + studioCreate + imageUpdate)"
        )

        calls = graphql_route.calls
        # Call 0: findImage (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findImage" in req0["query"]
        assert req0["variables"]["id"] == "456"
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

        # Call 3: findStudios (creator studio lookup - not found)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert "studio_filter" in req3["variables"]
        resp3 = calls[3].response.json()
        assert resp3["data"]["findStudios"]["count"] == 0

        # Call 4: studioCreate
        req4 = json.loads(calls[4].request.content)
        assert "studioCreate" in req4["query"]
        assert req4["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp4 = calls[4].response.json()
        assert resp4["data"]["studioCreate"]["id"] == "1123"

        # Call 5: imageUpdate
        req5 = json.loads(calls[5].request.content)
        assert "imageUpdate" in req5["query"]
        assert req5["variables"]["input"]["id"] == "456"
        resp5 = calls[5].response.json()
        assert resp5["data"]["imageUpdate"]["id"] == "456"

    @pytest.mark.asyncio
    async def test_process_media_with_stash_id(self, respx_stash_processor):
        """Test _process_media method with stash_id.

        Unit test using respx - verifies stash_id lookup path.
        """
        # Create test data using factories
        acct_id = snowflake_id()
        post_id = snowflake_id()
        media_id = snowflake_id()

        account = AccountFactory.build(id=acct_id, username="test_user")
        item = PostFactory.build(id=post_id, accountId=acct_id)

        # Create Media with stash_id
        media = MediaFactory.build(
            id=media_id,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
            stash_id=123,
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
            "id": "2456",
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
            id="123",
            title="Test Scene",
            files=[video_file],
        )

        # Response 2: findPerformers (not found)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate
        creator_studio = create_studio_dict(
            id="1123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 6: sceneUpdate result
        updated_scene = create_scene_dict(
            id="123",
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
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
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
        assert result["scenes"][0].id == "123"
        assert len(result["images"]) == 0

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        assert len(graphql_route.calls) == 6, (
            "Expected exactly 5 GraphQL calls after ORM migration + store.save() fix"
        )

        calls = graphql_route.calls
        # Call 0: findScene (by stash_id)
        req0 = json.loads(calls[0].request.content)
        assert "findScene" in req0["query"]
        assert req0["variables"]["id"] == "123"
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

        # Call 3: findStudios (creator studio lookup - not found)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert "studio_filter" in req3["variables"]
        resp3 = calls[3].response.json()
        assert resp3["data"]["findStudios"]["count"] == 0

        # Call 4: studioCreate (no verification search needed after store.save() fix)
        req4 = json.loads(calls[4].request.content)
        assert "studioCreate" in req4["query"]
        assert req4["variables"]["input"]["name"] == f"{account.username} (Fansly)"
        resp4 = calls[4].response.json()
        assert resp4["data"]["studioCreate"]["id"] == "1123"

        # Call 5: sceneUpdate (not imageUpdate - this is a video)
        req5 = json.loads(calls[5].request.content)
        assert "sceneUpdate" in req5["query"]
        assert req5["variables"]["input"]["id"] == "123"
        resp5 = calls[5].response.json()
        assert resp5["data"]["sceneUpdate"]["id"] == "123"

    @pytest.mark.asyncio
    async def test_process_media_with_variants(self, respx_stash_processor):
        """Test _process_media method with variants.

        Unit test using respx - verifies path-based lookup includes parent + variant IDs.
        """
        # Create test data using factories
        acct_id = snowflake_id()
        post_id = snowflake_id()
        media_id = snowflake_id()
        variant_id_1 = snowflake_id()
        variant_id_2 = snowflake_id()

        account = AccountFactory.build(id=acct_id, username="test_user")
        item = PostFactory.build(id=post_id, accountId=acct_id)

        # Create variant Media objects
        variant1 = MediaFactory.build(
            id=variant_id_1,
            mimetype="image/jpeg",
            accountId=account.id,
        )
        variant2 = MediaFactory.build(
            id=variant_id_2,
            mimetype="video/mp4",
            accountId=account.id,
        )
        variants = {variant1, variant2}

        # Create parent Media with variants (NO stash_id, so path lookup)
        media = MediaFactory.build(
            id=media_id,
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
            "id": "10001",
            "path": "/path/to/media_7891.jpg",
            "basename": "media_7891.jpg",
            "size": 1024,
            "width": 1920,
            "height": 1080,
            "format": "jpg",
            "parent_folder_id": None,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        image_result = create_image_dict(
            id="10002",
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
            "id": "2789",
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
            id="5789",
            title="Test Parent Scene",
            files=[parent_video_file],
        )

        # Variant scene (ID=7892)
        variant_video_file = {
            "__typename": "VideoFile",
            "id": "10003",
            "path": "/path/to/media_7892.mp4",
            "basename": "media_7892.mp4",
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
            id="10004",
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
            id="246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 5: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 6, 10, 14: studioCreate (FILE 1 creates, FILES 2-3 try to create again)
        creator_studio = create_studio_dict(
            id="1123",
            name=f"{account.username} (Fansly)",
            urls=[f"https://fansly.com/{account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 7: imageUpdate (image variant)
        updated_image = create_image_dict(
            id="10002",
            title="Test Image Variant",
            visual_files=[image_file],
            studio=creator_studio,
        )

        # Response 8: sceneUpdate (parent scene)
        updated_parent_scene = create_scene_dict(
            id="5789",
            title="Test Parent Scene",
            files=[parent_video_file],
            studio=creator_studio,
        )

        # Response 9: sceneUpdate (scene variant)
        updated_variant_scene = create_scene_dict(
            id="10004",
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
                # FILE 1 (image variant): full performer + studio lookups
                # Call 2: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 3: findStudios (Fansly network)
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # Call 4: findStudios (creator studio - not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # Call 5: studioCreate (creator studio)
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # Call 6: imageUpdate
                httpx.Response(
                    200, json=create_graphql_response("imageUpdate", updated_image)
                ),
                # FILE 2 (parent scene): studios cached from file 1
                # Call 7: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 8: sceneUpdate (studios served from sync filter() cache)
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", updated_parent_scene),
                ),
                # FILE 3 (scene variant): studios cached from file 1
                # Call 9: findPerformers
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # Call 10: sceneUpdate (studios served from sync filter() cache)
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
        assert result["images"][0].id == "10002"
        assert len(result["scenes"]) == 2
        scene_ids = {s.id for s in result["scenes"]}
        assert "5789" in scene_ids, "Parent scene should be in result"
        assert "10004" in scene_ids, "Variant scene should be in result"

        # Verify GraphQL call sequence (permanent assertion to catch regressions)
        # Cache-first pattern: file 1 populates studio cache, files 2-3 skip studio lookups
        # Sequence: 2 path finds +
        #   FILE1: (performer + fansly + creator_find + studioCreate + imageUpdate) = 5
        #   FILE2: (performer + sceneUpdate) = 2 (studios cached)
        #   FILE3: (performer + sceneUpdate) = 2 (studios cached)
        # Total: 11 calls
        assert len(graphql_route.calls) == 11, (
            f"Expected 11 calls with cache-first studio lookups, got {len(graphql_route.calls)}"
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

        # FILE 1 (image variant) - Calls 2-6
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

        # Call 4: findStudios (creator studio - not found)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        resp4 = calls[4].response.json()
        assert resp4["data"]["findStudios"]["count"] == 0

        # Call 5: studioCreate
        req5 = json.loads(calls[5].request.content)
        assert "studioCreate" in req5["query"]
        resp5 = calls[5].response.json()
        assert resp5["data"]["studioCreate"]["id"] == "1123"

        # Call 6: imageUpdate
        req6 = json.loads(calls[6].request.content)
        assert "imageUpdate" in req6["query"]
        assert req6["variables"]["input"]["id"] == "10002"

        # FILE 2 (parent scene) - Calls 7-8 (studios cached from file 1)
        # Call 7: findPerformers
        req7 = json.loads(calls[7].request.content)
        assert "findPerformers" in req7["query"]

        # Call 8: sceneUpdate (parent scene — studios served from cache)
        req8 = json.loads(calls[8].request.content)
        assert "sceneUpdate" in req8["query"]
        assert req8["variables"]["input"]["id"] == "5789"

        # FILE 3 (scene variant) - Calls 9-10 (studios cached from file 1)
        # Call 9: findPerformers
        req9 = json.loads(calls[9].request.content)
        assert "findPerformers" in req9["query"]

        # Call 10: sceneUpdate (scene variant — studios served from cache)
        req10 = json.loads(calls[10].request.content)
        assert "sceneUpdate" in req10["query"]
        assert req10["variables"]["input"]["id"] == "10004"
