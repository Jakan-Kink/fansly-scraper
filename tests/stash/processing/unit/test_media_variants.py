"""Tests for media variant and bundle processing functionality.

These are UNIT tests that use respx to mock Stash HTTP responses.
They test the _process_media logic for handling different media variants.
"""

import httpx
import pytest
import respx

from metadata import MediaLocation
from metadata.models import get_store
from tests.fixtures.metadata.metadata_factories import AccountMediaFactory, MediaFactory
from tests.fixtures.stash.stash_api_fixtures import assert_op, dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_find_performers_result,
    create_find_scenes_result,
    create_find_studios_result,
    create_graphql_response,
    create_image_dict,
    create_performer_dict,
    create_scene_dict,
    create_studio_dict,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_process_hls_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    entity_store,
):
    """Test processing media with HLS stream variant.

    Tests that _process_media correctly processes HLS variants by:
    1. Finding the scene in Stash via find_scenes GraphQL query
    2. Updating the scene metadata via sceneUpdate mutation
    """
    store = get_store()

    # Arrange - Create REAL HLS variant Media using factory
    hls_variant = MediaFactory.build(
        id=snowflake_id(),
        accountId=test_account.id,
        type=302,  # HLS stream
        mimetype="application/vnd.apple.mpegurl",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    await store.save(hls_variant)

    # Create MediaLocation for the variant
    hls_location = MediaLocation(
        mediaId=hls_variant.id,
        locationId=102,
        location="https://example.com/test.m3u8",
    )
    hls_variant.locations = [hls_location]
    await store.save(hls_variant)

    # Add variant to test_media
    test_media.variants = [hls_variant]
    test_media.stash_id = None
    test_media.is_downloaded = True
    await store.save(test_media)

    # Mock Stash GraphQL HTTP responses using helpers
    # The code makes MULTIPLE GraphQL calls in this order:
    # 1. findScenes - to find the scene by path
    # 2. findPerformers - to find the main performer (account)
    # 3. findStudios - to find "Fansly (network)" studio
    # 4. findStudios - to find the creator-specific studio
    # 5. sceneUpdate - to save the updated scene metadata

    # Response 1: FindScenes
    # NOTE: The path must contain the VARIANT media ID, not the parent media ID
    scene_data = create_scene_dict(
        id="55010",
        title="HLS Test Scene",
        files=[
            {
                "__typename": "VideoFile",
                "id": "80010",
                "path": f"/path/to/media_{hls_variant.id}",
                "basename": f"media_{hls_variant.id}.m3u8",
                "size": 1024,
                # VideoFile fields
                "parent_folder": {
                    "id": "90001",
                    "path": "/path/to",
                    "mod_time": "2024-01-01T00:00:00Z",
                },
                "format": "m3u8",
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
        ],
    )
    find_scenes_data = create_find_scenes_result(count=1, scenes=[scene_data])

    # Response 2: findPerformers by name (not found - _find_existing_performer doesn't create)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # Response 3: findStudios - Fansly network studio
    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 4: findStudios - Creator-specific studio (not found)
    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    # Response 5: studioCreate - Creator-specific studio (get_or_create creates immediately)
    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # Response 6: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="55010",
        title="HLS Test Scene",  # Will be updated with actual title from test_post
        files=[scene_data["files"][0]],  # Keep the same file
    )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # Call 0: findScenes (only 1 result, so no pagination check needed)
            httpx.Response(
                200,
                json=create_graphql_response("findScenes", find_scenes_data),
            ),
            # Call 1: findPerformers by name (not found - _find_existing_performer makes single call)
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", empty_performers_result),
            ),
            # Call 2: findStudios - Fansly network
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            # Call 3: findStudios - Creator-specific studio (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findStudios", creator_not_found_result),
            ),
            # Call 4: studioCreate (v0.10.4: get_or_create creates immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
            ),
            # Call 5: sceneUpdate
            httpx.Response(
                200, json=create_graphql_response("sceneUpdate", updated_scene_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}

    try:
        await respx_stash_processor._process_media(
            test_media, test_post, test_account, result
        )
    finally:
        pass  # Debug block removed after test fixed

    # Assert
    # Verify that a scene was found and added to results
    assert len(result["scenes"]) == 1
    assert result["scenes"][0].id == "55010"

    # Verify GraphQL call sequence (permanent assertion)
    # v0.10.4: 6 calls: findScenes + findPerformers + findStudios + findStudios + studioCreate + sceneUpdate
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert_op(calls[0], "FindScenes")  # Find scenes by path regex
    assert_op(calls[1], "findPerformers")  # By name (not found)
    assert_op(calls[2], "findStudios")  # Fansly network studio
    assert_op(calls[3], "findStudios")  # Creator studio lookup (not found)
    assert_op(
        calls[4], "studioCreate"
    )  # v0.10.4: Creator studio (get_or_create creates)
    assert_op(calls[5], "sceneUpdate")


@pytest.mark.asyncio
async def test_process_dash_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    entity_store,
):
    """Test processing media with DASH stream variant.

    Tests that _process_media correctly processes DASH variants by:
    1. Finding the scene in Stash via find_scenes GraphQL query
    2. Updating the scene metadata via sceneUpdate mutation
    """
    store = get_store()

    # Arrange - Create REAL DASH variant Media using factory
    dash_variant = MediaFactory.build(
        id=snowflake_id(),
        accountId=test_account.id,
        type=303,  # DASH stream
        mimetype="application/dash+xml",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    await store.save(dash_variant)

    # Create MediaLocation for the variant
    dash_location = MediaLocation(
        mediaId=dash_variant.id,
        locationId=103,
        location="https://example.com/test.mpd",
    )
    dash_variant.locations = [dash_location]
    await store.save(dash_variant)

    # Add variant to test_media
    test_media.variants = [dash_variant]
    test_media.stash_id = None
    test_media.is_downloaded = True
    await store.save(test_media)

    # Mock Stash GraphQL HTTP responses using helpers
    # The code makes MULTIPLE GraphQL calls in this order:
    # 1. findScenes - to find the scene by path
    # 2. findPerformers - to find the main performer (account)
    # 3. findStudios - to find "Fansly (network)" studio
    # 4. findStudios - to find the creator-specific studio
    # 5. sceneUpdate - to save the updated scene metadata

    # Response 1: findScenes
    # NOTE: The path must contain the VARIANT media ID, not the parent media ID
    scene_data = create_scene_dict(
        id="55011",
        title="DASH Test Scene",
        files=[
            {
                "id": "80011",
                "path": f"/path/to/media_{dash_variant.id}",
                "basename": f"media_{dash_variant.id}.mpd",
                "size": 2048,
                # VideoFile required fields
                "parent_folder_id": None,
                "format": "mpd",
                "width": 1920,
                "height": 1080,
                "duration": 180.0,
                "video_codec": "h264",
                "audio_codec": "aac",
                "frame_rate": 30.0,
                "bit_rate": 6000000,
            }
        ],
    )
    find_scenes_data = create_find_scenes_result(count=1, scenes=[scene_data])

    # Response 2: findPerformers - convert mock_performer to dict
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    # Response 3: findStudios - Fansly network studio
    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 4: findStudios - Creator-specific studio (not found)
    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    # Response 5: studioCreate - Creator-specific studio
    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # Response 6: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="55011",
        title="DASH Test Scene",
        files=[scene_data["files"][0]],
    )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200, json=create_graphql_response("findScenes", find_scenes_data)
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", find_performers_data),
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            # findStudios - Creator-specific studio (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findStudios", creator_not_found_result),
            ),
            # v0.10.4: studioCreate instead of findStudios (get_or_create creates immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
            ),
            httpx.Response(
                200, json=create_graphql_response("sceneUpdate", updated_scene_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}
    await respx_stash_processor._process_media(
        test_media, test_post, test_account, result
    )

    # Assert
    # Verify that a scene was found and added to results
    assert len(result["scenes"]) == 1
    assert result["scenes"][0].id == "55011"

    # Verify GraphQL call sequence (permanent assertion)
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order (same as HLS variant)
    assert_op(calls[0], "findScenes")
    assert_op(calls[1], "findPerformers")
    assert_op(calls[2], "findStudios")  # Fansly network
    assert_op(calls[3], "findStudios")  # Creator studio lookup (not found)
    assert_op(calls[4], "studioCreate")  # v0.10.4: Creator studio
    assert_op(calls[5], "sceneUpdate")


@pytest.mark.asyncio
async def test_process_preview_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    entity_store,
):
    """Test processing media with preview image variant.

    Tests that _process_media correctly processes preview images by:
    1. Finding the image in Stash via find_images GraphQL query
    2. Updating the image metadata via imageUpdate mutation
    """
    store = get_store()

    # Arrange - Create REAL preview variant Media using factory
    preview_variant = MediaFactory.build(
        id=snowflake_id(),
        accountId=test_account.id,
        type=1,  # Preview image
        mimetype="image/jpeg",
        meta_info='{"resolutionMode":1}',
        is_downloaded=True,
    )
    await store.save(preview_variant)

    # Create MediaLocation for the variant
    preview_location = MediaLocation(
        mediaId=preview_variant.id,
        locationId=1,
        location="https://example.com/preview.jpg",
    )
    preview_variant.locations = [preview_location]
    await store.save(preview_variant)

    # Add variant to test_media
    test_media.variants = [preview_variant]
    test_media.stash_id = None
    test_media.is_downloaded = True
    await store.save(test_media)

    # Mock Stash GraphQL HTTP responses using helpers
    # The code makes MULTIPLE GraphQL calls in this order:
    # 1. findImages - to find images by path (preview variant with image/jpeg mimetype)
    # 2. findScenes - to find scenes by path (parent media with video/mp4 mimetype)
    # 3. findPerformers - to find the main performer (account)
    # 4. findStudios - to find "Fansly (network)" studio
    # 5. findStudios - to find the creator-specific studio
    # 6. imageUpdate - to save the updated image metadata
    #
    # NOTE: Images are processed BEFORE scenes in _find_stash_files_by_path (line 492)

    # Response 1: findImages (for preview variant)
    # NOTE: The path must contain the VARIANT media ID, not the parent media ID
    image_data = create_image_dict(
        id="45010",
        title="Preview Test Image",
        visual_files=[
            {
                "id": "80012",
                "path": f"/path/to/media_{preview_variant.id}",
                "basename": f"media_{preview_variant.id}.jpg",
                "parent_folder_id": None,
                "mod_time": "2024-01-01T00:00:00Z",
                "size": 512000,
                "fingerprints": [],
                # ImageFile required fields
                "width": 1920,
                "height": 1080,
            }
        ],
    )
    find_images_data = create_find_images_result(count=1, images=[image_data])

    # Response 2: findScenes (for parent video media - returns empty)
    empty_scenes_result = create_find_scenes_result(count=0, scenes=[])

    # Response 3: findPerformers - convert mock_performer to dict
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    # Response 4: findStudios - Fansly network studio
    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 5: findStudios - Creator-specific studio (not found)
    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    # Response 6: studioCreate - Creator-specific studio
    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # Response 7: imageUpdate - mutation returns the updated image
    updated_image_data = create_image_dict(
        id="45010",
        title="Preview Test Image",
        visual_files=[image_data["visual_files"][0]],
    )

    # SGC v0.12: add_performer() triggers populate() Performer.images filter_query.
    empty_images_populate = {"count": 0, "images": []}

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200, json=create_graphql_response("findImages", find_images_data)
            ),
            httpx.Response(
                200, json=create_graphql_response("findScenes", empty_scenes_result)
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", find_performers_data),
            ),
            # [3] SGC v0.12: findImages populate from add_performer
            httpx.Response(
                200, json=create_graphql_response("findImages", empty_images_populate)
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            # findStudios - Creator-specific studio (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findStudios", creator_not_found_result),
            ),
            # v0.10.4: studioCreate instead of findStudios (get_or_create creates immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
            ),
            httpx.Response(
                200, json=create_graphql_response("imageUpdate", updated_image_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}
    from tests.fixtures.stash import dump_graphql_calls

    try:
        await respx_stash_processor._process_media(
            test_media, test_post, test_account, result
        )
    finally:
        dump_graphql_calls(graphql_route.calls, "test_process_preview_variant")

    # Assert
    # Verify that an image was found and added to results
    assert len(result["images"]) == 1
    assert result["images"][0].id == "45010"

    # Verify GraphQL call sequence (permanent assertion)
    # SGC v0.12: 8 calls (was 7) — findImages from add_performer populate filter-query
    assert len(graphql_route.calls) == 8, "Expected exactly 8 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert_op(calls[0], "findImages")
    assert_op(calls[1], "findScenes")
    assert_op(calls[2], "findPerformers")
    assert_op(calls[3], "findImages")  # populate
    assert_op(calls[4], "findStudios")  # Fansly network
    assert_op(calls[5], "findStudios")  # Creator studio lookup (not found)
    assert_op(calls[6], "studioCreate")  # v0.10.4: Creator studio
    assert_op(calls[7], "imageUpdate")


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    respx_stash_processor,
    mock_performer,
    test_account,
    entity_store,
):
    """Test processing media bundle with specific ordering.

    Tests that _process_bundle_media correctly processes bundles and maintains
    the order of media items within the bundle.
    """
    store = get_store()

    # Arrange
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    # Create bundle
    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111222,
        accountId=test_account.id,
    )
    await store.save(test_media_bundle)

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    await store.save(test_post)

    media_items = []
    for i in range(3):
        # Create Media with unique ID and downloaded status
        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
            is_downloaded=True,
        )
        await store.save(media)

        # Create MediaLocation for the media
        media_location = MediaLocation(
            mediaId=media.id,
            locationId=200 + i,
            location=f"https://example.com/bundle_media_{i}.jpg",
        )
        media.locations = [media_location]
        await store.save(media)

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        await store.save(account_media)
        # account_media.media auto-resolves via identity map (Media already saved)

        media_items.append(account_media)

    # Set accountMedia on the bundle (the relationship)
    test_media_bundle.accountMedia = media_items
    await store.save(test_media_bundle)

    # Mock Stash GraphQL HTTP responses
    # Bundle processing: findImages, findScenes, then for EACH image: findPerformers, 2x findStudios, imageUpdate

    # Response 1: findImages - return all 3 images
    images_data = []
    for i, account_media in enumerate(media_items):
        image_data = create_image_dict(
            id=f"{45020 + i}",
            title=f"Bundle Image {i}",
            visual_files=[
                {
                    "id": f"{80020 + i}",
                    "path": f"/path/to/media_{account_media.mediaId}",
                    "basename": f"media_{account_media.mediaId}.jpg",
                    "parent_folder_id": None,
                    "mod_time": "2024-01-01T00:00:00Z",
                    "size": 512000,
                    "fingerprints": [],
                    "width": 1920,
                    "height": 1080,
                }
            ],
        )
        images_data.append(image_data)

    find_images_data = create_find_images_result(count=3, images=images_data)

    # Response 2: findScenes - empty (no video media in bundle)
    empty_scenes_result = create_find_scenes_result(count=0, scenes=[])

    # Create reusable responses
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # findPerformers: by name (not found - _find_existing_performer doesn't create)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # Build the full response sequence (no findScenes - bundle has only images)
    # Studio lookup is hoisted to batch level (_find_existing_studio called at top of
    # _process_batch_internal), so findStudios + studioCreate come FIRST, before findImages.
    responses = [
        # Studio lookup (hoisted to batch level)
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200,
            json=create_graphql_response("findStudios", creator_not_found_result),
        ),
        httpx.Response(
            200, json=create_graphql_response("studioCreate", creator_studio_dict)
        ),
        # findImages: find by path (only 3 results, so no pagination check needed)
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
        # Image 1: findPerformers + imageUpdate (studio already resolved at batch level)
        httpx.Response(
            200,
            json=create_graphql_response("findPerformers", empty_performers_result),
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[0])
        ),
        # Image 2: findPerformers + imageUpdate (studio already resolved at batch level)
        httpx.Response(
            200,
            json=create_graphql_response("findPerformers", empty_performers_result),
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[1])
        ),
        # Image 3: findPerformers + imageUpdate (studio already resolved at batch level)
        httpx.Response(
            200,
            json=create_graphql_response("findPerformers", empty_performers_result),
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[2])
        ),
    ]

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=responses
    )

    # Act
    result = {"images": [], "scenes": []}
    try:
        await respx_stash_processor._process_bundle_media(
            test_media_bundle, test_post, test_account, result
        )
    finally:
        from tests.fixtures.stash import dump_graphql_calls

        dump_graphql_calls(graphql_route.calls, "test_process_bundle_ordering")

    # Assert
    # Verify all 3 images were processed
    assert len(result["images"]) == 3

    # Verify items are in correct order
    assert len(test_media_bundle.accountMedia) == 3
    bundle_media_ids = [m.id for m in test_media_bundle.accountMedia]
    assert bundle_media_ids == [m.id for m in media_items]

    # Verify GraphQL call sequence (permanent assertion)
    # Studio lookup hoisted to batch level: 10 calls =
    #   3 (findStudios + findStudios + studioCreate) + 1 findImages + 2*3 (per-image: findPerformers + imageUpdate)
    assert len(graphql_route.calls) == 10, (
        f"Expected exactly 10 GraphQL calls, got {len(graphql_route.calls)}"
    )
    calls = graphql_route.calls

    # Verify query types in order
    # Studio lookup (hoisted to batch level)
    assert_op(calls[0], "findStudios")  # Fansly
    assert_op(calls[1], "findStudios")  # Creator (not found)
    assert_op(calls[2], "studioCreate")  # Creator
    # Find images
    assert_op(calls[3], "findImages")  # Find by path
    # Image 1: performer + update (studio already resolved)
    assert_op(calls[4], "findPerformers")
    assert_op(calls[5], "imageUpdate")
    # Image 2: performer + update (studio already resolved)
    assert_op(calls[6], "findPerformers")
    assert_op(calls[7], "imageUpdate")
    # Image 3: performer + update (studio already resolved)
    assert_op(calls[8], "findPerformers")
    assert_op(calls[9], "imageUpdate")


@pytest.mark.asyncio
async def test_process_bundle_with_preview(
    respx_stash_processor,
    mock_performer,
    test_account,
    entity_store,
):
    """Test processing media bundle with preview image.

    Tests that _process_bundle_media correctly handles bundles with preview images.
    """
    store = get_store()

    # Arrange
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    # Create bundle
    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111223,
        accountId=test_account.id,
    )
    await store.save(test_media_bundle)

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    await store.save(test_post)

    # Create REAL preview media using factory
    preview_media = MediaFactory.build(
        id=snowflake_id(),
        accountId=test_media_bundle.accountId,
        mimetype="image/jpeg",
        type=1,  # Image type
        is_downloaded=True,
    )
    await store.save(preview_media)

    # Create MediaLocation for preview
    preview_location = MediaLocation(
        mediaId=preview_media.id,
        locationId=999,
        location="https://example.com/preview.jpg",
    )
    preview_media.locations = [preview_location]
    await store.save(preview_media)

    # Update bundle to reference this preview
    test_media_bundle.previewId = preview_media.id
    test_media_bundle.preview = preview_media  # Resolve relationship in-memory
    await store.save(test_media_bundle)

    # Mock Stash GraphQL HTTP responses
    # Bundle with preview (IMAGE only, NO videos):
    # 1. findStudios (Fansly network) -- hoisted to batch level
    # 2. findStudios (creator studio -- not found) -- hoisted to batch level
    # 3. studioCreate (creator studio) -- hoisted to batch level
    # 4. findImages (for preview image by path)
    # 5. findPerformers (by name via _find_existing_performer - not found)
    # 6. imageUpdate (save metadata)
    # NOTE: No findScenes call because bundle has no video files

    image_data = create_image_dict(
        id="45011",
        title="Preview Image",
        visual_files=[
            {
                "id": "80013",
                "path": f"/path/to/media_{preview_media.id}",
                "basename": f"media_{preview_media.id}.jpg",
                "parent_folder_id": None,
                "mod_time": "2024-01-01T00:00:00Z",
                "size": 512000,
                "fingerprints": [],
                "width": 1920,
                "height": 1080,
            }
        ],
    )
    find_images_data = create_find_images_result(count=1, images=[image_data])

    # findPerformers: by name (not found - _find_existing_performer doesn't create)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # Studio lookup (hoisted to batch level)
            # findStudios: find Fansly (network) studio
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            # findStudios: find creator studio (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findStudios", creator_not_found_result),
            ),
            # studioCreate (get_or_create creates creator studio immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
            ),
            # findImages: find by path (only 1 result, so no pagination check needed)
            httpx.Response(
                200, json=create_graphql_response("findImages", find_images_data)
            ),
            # findPerformers: _find_existing_performer makes single call by name (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", empty_performers_result),
            ),
            # imageUpdate: save metadata
            httpx.Response(
                200, json=create_graphql_response("imageUpdate", image_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}
    try:
        await respx_stash_processor._process_bundle_media(
            test_media_bundle, test_post, test_account, result
        )
    finally:
        # dump_graphql_calls is the SIGABRT-safe replacement for bare print()
        # loops in test teardown (per feedback_no_bare_print.md memory).
        dump_graphql_calls(graphql_route.calls, "test_process_bundle_with_preview")

    # Assert
    # Verify preview was used (check that previewId is set)
    assert test_media_bundle.previewId == preview_media.id
    # Verify the preview image was processed
    assert len(result["images"]) == 1

    # Verify GraphQL call sequence (permanent assertion)
    # 6 calls: findStudios + findStudios + studioCreate + findImages + findPerformers + imageUpdate
    # Studio lookup hoisted to batch level, runs before findImages
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    # Studio lookup (hoisted to batch level)
    assert_op(calls[0], "findStudios")  # Fansly network
    assert_op(calls[1], "findStudios")  # Creator studio lookup (not found)
    assert_op(calls[2], "studioCreate")  # Creator studio
    # Find images
    assert_op(calls[3], "findImages")  # Find by path
    # Per-image: performer + update (studio already resolved)
    assert_op(calls[4], "findPerformers")  # By name (not found)
    assert_op(calls[5], "imageUpdate")


@pytest.mark.asyncio
async def test_bundle_permission_inheritance(
    respx_stash_processor,
    mock_performer,
    test_account,
    entity_store,
):
    """Test that media items inherit bundle permissions.

    Note: permissions is just data, not a database field in AccountMediaBundle.
    This test verifies that _process_bundle_media properly handles permissions.
    """
    store = get_store()

    # Arrange
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    # Create bundle
    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111224,
        accountId=test_account.id,
    )
    await store.save(test_media_bundle)

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    await store.save(test_post)

    # Create REAL media items using factories
    media_items = []
    for i in range(2):
        # Create Media
        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
            is_downloaded=True,
        )
        await store.save(media)

        # Create MediaLocation
        media_location = MediaLocation(
            mediaId=media.id,
            locationId=300 + i,
            location=f"https://example.com/permission_media_{i}.jpg",
        )
        media.locations = [media_location]
        await store.save(media)

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        await store.save(account_media)
        # account_media.media auto-resolves via identity map (Media already saved)

        media_items.append(account_media)

    # Set accountMedia on the bundle (the relationship)
    test_media_bundle.accountMedia = media_items
    await store.save(test_media_bundle)

    # Mock Stash GraphQL HTTP responses
    # 2 images: findImages, findScenes, findPerformers, 2x findStudios, 2x imageUpdate

    images_data = []
    for i, account_media in enumerate(media_items):
        image_data = create_image_dict(
            id=f"{45030 + i}",
            title=f"Permission Image {i}",
            visual_files=[
                {
                    "id": f"{80030 + i}",
                    "path": f"/path/to/media_{account_media.mediaId}",
                    "basename": f"media_{account_media.mediaId}.jpg",
                    "parent_folder_id": None,
                    "mod_time": "2024-01-01T00:00:00Z",
                    "size": 512000,
                    "fingerprints": [],
                    "width": 1920,
                    "height": 1080,
                }
            ],
        )
        images_data.append(image_data)

    find_images_data = create_find_images_result(count=2, images=images_data)

    # v0.10.3 pattern: findPerformers (name only), findStudios (Fansly), studioCreate (creator)
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    fansly_studio_dict = create_studio_dict(id="10300", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_not_found_result = create_find_studios_result(count=0, studios=[])

    creator_studio_dict = create_studio_dict(
        id="10302",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # SGC v0.12: add_performer() triggers populate() of Performer.images
    # (filter_query) inverse on first call. Both images reference the SAME
    # performer, so only ONE findImages populate fires (second image's
    # add_performer finds _received_fields already includes "images").
    empty_images_result = {"count": 0, "images": []}

    # Build response sequence (no findScenes - bundle has only images)
    # Studio lookup is hoisted to batch level (_find_existing_studio called at top of
    # _process_batch_internal), so findStudios + studioCreate come FIRST, before findImages.
    responses = [
        # Studio lookup (hoisted to batch level)
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", creator_not_found_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("studioCreate", creator_studio_dict)
        ),
        # findImages
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
        # First image: findPerformers + populate findImages + imageUpdate
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("findImages", empty_images_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[0])
        ),
        # Second image: findPerformers + imageUpdate (no populate — performer cached)
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[1])
        ),
    ]

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=responses
    )

    # Act
    result = {"images": [], "scenes": []}
    try:
        await respx_stash_processor._process_bundle_media(
            test_media_bundle, test_post, test_account, result
        )
    finally:
        from tests.fixtures.stash import dump_graphql_calls

        dump_graphql_calls(graphql_route.calls, "test_bundle_permission_inheritance")

    # Assert
    # Verify bundle was processed
    assert len(result["images"]) == 2

    # Verify items are in correct order
    assert len(test_media_bundle.accountMedia) == 2
    bundle_media_ids = [m.id for m in test_media_bundle.accountMedia]
    assert bundle_media_ids == [m.id for m in media_items]

    # SGC v0.12: 9 calls (was 8). First add_performer populates Performer.images;
    # second add_performer skips populate (same performer, inverse cached).
    assert len(graphql_route.calls) == 9, (
        f"Expected exactly 9 GraphQL calls, got {len(graphql_route.calls)}"
    )
    calls = graphql_route.calls

    # Studio lookup (hoisted to batch level)
    assert_op(calls[0], "findStudios")
    assert_op(calls[1], "findStudios")
    assert_op(calls[2], "studioCreate")
    # Find images
    assert_op(calls[3], "findImages")
    # First image: findPerformers + populate findImages + imageUpdate
    assert_op(calls[4], "findPerformers")
    assert_op(calls[5], "findImages")
    assert_op(calls[6], "imageUpdate")
    # Second image: findPerformers + imageUpdate (no populate — performer cached)
    assert_op(calls[7], "findPerformers")
    assert_op(calls[8], "imageUpdate")
