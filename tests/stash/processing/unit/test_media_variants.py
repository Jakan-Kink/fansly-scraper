"""Tests for media variant and bundle processing functionality.

These are UNIT tests that use respx to mock Stash HTTP responses.
They test the _process_media logic for handling different media variants.
"""

import json

import httpx
import pytest
import respx

from tests.fixtures import MediaLocationFactory
from tests.fixtures.metadata.metadata_factories import AccountMediaFactory, MediaFactory
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


@pytest.mark.asyncio
async def test_process_hls_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    session,
):
    """Test processing media with HLS stream variant.

    Tests that _process_media correctly processes HLS variants by:
    1. Finding the scene in Stash via find_scenes GraphQL query
    2. Updating the scene metadata via sceneUpdate mutation
    """
    # Arrange - Create REAL HLS variant Media using factory
    hls_variant = MediaFactory.build(
        id=100102,
        accountId=test_account.id,
        type=302,  # HLS stream
        mimetype="application/vnd.apple.mpegurl",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    session.add(hls_variant)
    await session.commit()

    # Create MediaLocation for the variant
    hls_location = MediaLocationFactory.build(
        mediaId=hls_variant.id,
        locationId=102,
        location="https://example.com/test.m3u8",
    )
    session.add(hls_location)
    await session.commit()

    # Add variant to test_media - use async session to avoid lazy-load issues
    # First, ensure all objects are in the session and load existing relationships
    await session.refresh(test_media, attribute_names=["variants"])
    test_media.variants = {hls_variant}
    test_media.stash_id = None
    test_media.is_downloaded = True
    session.add(test_media)
    await session.commit()

    # Mock Stash GraphQL HTTP responses using helpers
    # The code makes MULTIPLE GraphQL calls in this order:
    # 1. findScenes - to find the scene by path
    # 2. findPerformers - to find the main performer (account)
    # 3. findStudios - to find "Fansly (network)" studio
    # 4. findStudios - to find the creator-specific studio
    # 5. sceneUpdate - to save the updated scene metadata

    # Response 1: findScenesByPathRegex
    # NOTE: The path must contain the VARIANT media ID, not the parent media ID
    scene_data = create_scene_dict(
        id="scene_123",
        title="HLS Test Scene",
        files=[
            {
                "__typename": "VideoFile",
                "id": "file_123",
                "path": f"/path/to/media_{hls_variant.id}",
                "basename": f"media_{hls_variant.id}.m3u8",
                "size": 1024,
                # VideoFile required fields
                "parent_folder_id": None,
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

    # Response 2-3: findPerformers (name + alias searches)
    empty_performers_result = create_find_performers_result(count=0, performers=[])
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    # Response 3: findStudios - Fansly network studio
    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 4: findStudios - Creator-specific studio
    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Response 6: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="scene_123",
        title="HLS Test Scene",  # Will be updated with actual title from test_post
        files=[scene_data["files"][0]],  # Keep the same file
    )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findScenesByPathRegex", find_scenes_data),
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", empty_performers_result),
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", find_performers_data),
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", creator_studio_result)
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
    assert result["scenes"][0].id == "scene_123"

    # Verify GraphQL call sequence (permanent assertion)
    import json

    # 6 calls: findScenesByPathRegex + 2 findPerformers + 2 findStudios + sceneUpdate
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    req0 = json.loads(calls[0].request.content)
    assert "findScenesByPathRegex" in req0["query"]

    req1 = json.loads(calls[1].request.content)
    assert "findPerformers" in req1["query"]

    req2 = json.loads(calls[2].request.content)
    assert "findPerformers" in req2["query"]

    req3 = json.loads(calls[3].request.content)
    assert "findStudios" in req3["query"]

    req4 = json.loads(calls[4].request.content)
    assert "findStudios" in req4["query"]

    req5 = json.loads(calls[5].request.content)
    assert "sceneUpdate" in req5["query"]


@pytest.mark.asyncio
async def test_process_dash_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    session,
):
    """Test processing media with DASH stream variant.

    Tests that _process_media correctly processes DASH variants by:
    1. Finding the scene in Stash via find_scenes GraphQL query
    2. Updating the scene metadata via sceneUpdate mutation
    """
    # Arrange - Create REAL DASH variant Media using factory
    dash_variant = MediaFactory.build(
        id=100103,
        accountId=test_account.id,
        type=303,  # DASH stream
        mimetype="application/dash+xml",
        meta_info='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        is_downloaded=True,
    )
    session.add(dash_variant)
    await session.commit()

    # Create MediaLocation for the variant
    dash_location = MediaLocationFactory.build(
        mediaId=dash_variant.id,
        locationId=103,
        location="https://example.com/test.mpd",
    )
    session.add(dash_location)
    await session.commit()

    # Add variant to test_media - use async session to avoid lazy-load issues
    # First, ensure all objects are in the session and load existing relationships
    await session.refresh(test_media, attribute_names=["variants"])
    test_media.variants = {dash_variant}
    test_media.stash_id = None
    test_media.is_downloaded = True
    session.add(test_media)
    await session.commit()

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
        id="scene_456",
        title="DASH Test Scene",
        files=[
            {
                "id": "file_456",
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
    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 4: findStudios - Creator-specific studio
    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Response 5: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="scene_456",
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
            httpx.Response(
                200, json=create_graphql_response("findStudios", creator_studio_result)
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
    assert result["scenes"][0].id == "scene_456"

    # Verify GraphQL call sequence (permanent assertion)
    assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order (same as HLS variant)
    assert "findScenes" in json.loads(calls[0].request.content)["query"]
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]
    assert "findStudios" in json.loads(calls[2].request.content)["query"]
    assert "findStudios" in json.loads(calls[3].request.content)["query"]
    assert "sceneUpdate" in json.loads(calls[4].request.content)["query"]


@pytest.mark.asyncio
async def test_process_preview_variant(
    respx_stash_processor,
    test_media,
    mock_performer,
    test_account,
    test_post,
    session,
):
    """Test processing media with preview image variant.

    Tests that _process_media correctly processes preview images by:
    1. Finding the image in Stash via find_images GraphQL query
    2. Updating the image metadata via imageUpdate mutation
    """
    # Arrange - Create REAL preview variant Media using factory
    preview_variant = MediaFactory.build(
        id=100001,
        accountId=test_account.id,
        type=1,  # Preview image
        mimetype="image/jpeg",
        meta_info='{"resolutionMode":1}',
        is_downloaded=True,
    )
    session.add(preview_variant)
    await session.commit()

    # Create MediaLocation for the variant
    preview_location = MediaLocationFactory.build(
        mediaId=preview_variant.id,
        locationId=1,
        location="https://example.com/preview.jpg",
    )
    session.add(preview_location)
    await session.commit()

    # Add variant to test_media - use async session to avoid lazy-load issues
    # First, ensure all objects are in the session and load existing relationships
    await session.refresh(test_media, attribute_names=["variants"])
    test_media.variants = {preview_variant}
    test_media.stash_id = None
    test_media.is_downloaded = True
    session.add(test_media)
    await session.commit()

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
        id="image_789",
        title="Preview Test Image",
        visual_files=[
            {
                "id": "file_789",
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
    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 5: findStudios - Creator-specific studio
    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Response 6: imageUpdate - mutation returns the updated image
    updated_image_data = create_image_dict(
        id="image_789",
        title="Preview Test Image",
        visual_files=[image_data["visual_files"][0]],
    )

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
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", creator_studio_result)
            ),
            httpx.Response(
                200, json=create_graphql_response("imageUpdate", updated_image_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}
    await respx_stash_processor._process_media(
        test_media, test_post, test_account, result
    )

    # Assert
    # Verify that an image was found and added to results
    assert len(result["images"]) == 1
    assert result["images"][0].id == "image_789"

    # Verify GraphQL call sequence (permanent assertion)
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]
    assert "findScenes" in json.loads(calls[1].request.content)["query"]
    assert "findPerformers" in json.loads(calls[2].request.content)["query"]
    assert "findStudios" in json.loads(calls[3].request.content)["query"]
    assert "findStudios" in json.loads(calls[4].request.content)["query"]
    assert "imageUpdate" in json.loads(calls[5].request.content)["query"]


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    respx_stash_processor,
    mock_performer,
    test_account,
    session,
):
    """Test processing media bundle with specific ordering.

    Tests that _process_bundle_media correctly processes bundles and maintains
    the order of media items within the bundle.
    """
    # Arrange
    # Create multiple REAL media items in bundle using factories
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory

    # Create bundle fresh in the async session
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111222,
        accountId=test_account.id,
    )
    session.add(test_media_bundle)
    await session.commit()

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session.add(test_post)
    await session.commit()

    media_items = []
    for i in range(3):
        # Create Media with unique ID and downloaded status
        media = MediaFactory.build(
            id=200000 + i,  # Unique IDs for each media
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
            is_downloaded=True,
        )
        session.add(media)
        await session.commit()

        # Create MediaLocation for the media
        media_location = MediaLocationFactory.build(
            mediaId=media.id,
            locationId=200 + i,
            location=f"https://example.com/bundle_media_{i}.jpg",
        )
        session.add(media_location)
        await session.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        session.add(account_media)
        await session.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    # This is the proper way to add media to a bundle!
    for i, account_media in enumerate(media_items):
        await session.execute(
            account_media_bundle_media.insert().values(
                bundle_id=test_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    await session.commit()

    # Refresh bundle to load relationships
    await session.refresh(test_media_bundle, attribute_names=["accountMedia"])

    # Eagerly load media and preview relationships for each account_media
    for account_media in media_items:
        await session.refresh(account_media, attribute_names=["media", "preview"])

    # Mock Stash GraphQL HTTP responses
    # Bundle processing: findImages, findScenes, then for EACH image: findPerformers, 2x findStudios, imageUpdate

    # Response 1: findImages - return all 3 images
    images_data = []
    for i, account_media in enumerate(media_items):
        image_data = create_image_dict(
            id=f"image_{200000 + i}",
            title=f"Bundle Image {i}",
            visual_files=[
                {
                    "id": f"file_{200000 + i}",
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

    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # findPerformers call 1: by name (not found)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # Build the full response sequence (no findScenes - bundle has only images)
    # For each of 3 images: 2 findPerformers + 2 findStudios + 1 imageUpdate
    responses = [
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
    ]

    # Add responses for each image (performer/studio lookups not cached)
    for i in range(3):
        responses.extend(
            [
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_result
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", find_performers_data
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", creator_studio_result),
                ),
                httpx.Response(
                    200, json=create_graphql_response("imageUpdate", images_data[i])
                ),
            ]
        )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=responses
    )

    # Act
    result = {"images": [], "scenes": []}
    await respx_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify all 3 images were processed
    assert len(result["images"]) == 3

    # Verify items are in correct order by manually querying the junction table
    stmt = (
        account_media_bundle_media.select()
        .where(account_media_bundle_media.c.bundle_id == test_media_bundle.id)
        .order_by(account_media_bundle_media.c.pos)
    )
    result_rows = await session.execute(stmt)
    bundle_media_ids = [row.media_id for row in result_rows.all()]
    assert len(bundle_media_ids) == 3
    assert bundle_media_ids == [m.id for m in media_items]

    # Verify GraphQL call sequence (permanent assertion)
    # 16 calls: 1 findImages + (2 findPerformers + 2 findStudios + 1 imageUpdate) x 3 images
    assert len(graphql_route.calls) == 16, "Expected exactly 16 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]
    # Image 1
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]
    assert "findPerformers" in json.loads(calls[2].request.content)["query"]
    assert "findStudios" in json.loads(calls[3].request.content)["query"]
    assert "findStudios" in json.loads(calls[4].request.content)["query"]
    assert "imageUpdate" in json.loads(calls[5].request.content)["query"]
    # Image 2
    assert "findPerformers" in json.loads(calls[6].request.content)["query"]
    assert "findPerformers" in json.loads(calls[7].request.content)["query"]
    assert "findStudios" in json.loads(calls[8].request.content)["query"]
    assert "findStudios" in json.loads(calls[9].request.content)["query"]
    assert "imageUpdate" in json.loads(calls[10].request.content)["query"]
    # Image 3
    assert "findPerformers" in json.loads(calls[11].request.content)["query"]
    assert "findPerformers" in json.loads(calls[12].request.content)["query"]
    assert "findStudios" in json.loads(calls[13].request.content)["query"]
    assert "findStudios" in json.loads(calls[14].request.content)["query"]
    assert "imageUpdate" in json.loads(calls[15].request.content)["query"]


@pytest.mark.asyncio
async def test_process_bundle_with_preview(
    respx_stash_processor,
    mock_performer,
    test_account,
    session,
):
    """Test processing media bundle with preview image.

    Tests that _process_bundle_media correctly handles bundles with preview images.
    """
    # Arrange
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    # Create bundle fresh in the async session
    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111223,
        accountId=test_account.id,
    )
    session.add(test_media_bundle)
    await session.commit()

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session.add(test_post)
    await session.commit()

    # Create REAL preview media using factory
    preview_media = MediaFactory.build(
        id=123456,  # Specific ID for this test
        accountId=test_media_bundle.accountId,
        mimetype="image/jpeg",
        type=1,  # Image type
        is_downloaded=True,
    )
    session.add(preview_media)
    await session.commit()

    # Create MediaLocation for preview
    preview_location = MediaLocationFactory.build(
        mediaId=preview_media.id,
        locationId=999,
        location="https://example.com/preview.jpg",
    )
    session.add(preview_location)
    await session.commit()

    # Update bundle to reference this preview
    test_media_bundle.previewId = preview_media.id
    session.add(test_media_bundle)
    await session.commit()
    await session.refresh(test_media_bundle, attribute_names=["preview"])

    # Mock Stash GraphQL HTTP responses
    # Bundle with preview (IMAGE only, NO videos):
    # 1. findImages (for preview image)
    # 2. findPerformers by name (empty - not found by exact name match)
    # 3. findPerformers by alias (returns performer - found by alias)
    # 4. findStudios (Fansly network)
    # 5. findStudios (creator studio)
    # 6. imageUpdate (save metadata)
    # NOTE: No findScenes call because bundle has no video files

    image_data = create_image_dict(
        id="image_preview",
        title="Preview Image",
        visual_files=[
            {
                "id": "file_preview",
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

    # findPerformers call 1: by name (not found)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # findPerformers call 2: by alias (found)
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200, json=create_graphql_response("findImages", find_images_data)
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", empty_performers_result),
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", find_performers_data),
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            httpx.Response(
                200, json=create_graphql_response("findStudios", creator_studio_result)
            ),
            httpx.Response(
                200, json=create_graphql_response("imageUpdate", image_data)
            ),
        ]
    )

    # Act
    result = {"images": [], "scenes": []}
    await respx_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify preview was used (check that previewId is set)
    assert test_media_bundle.previewId == preview_media.id
    # Verify the preview image was processed
    assert len(result["images"]) == 1

    # Verify GraphQL call sequence (permanent assertion)
    assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]  # By name
    assert "findPerformers" in json.loads(calls[2].request.content)["query"]  # By alias
    assert "findStudios" in json.loads(calls[3].request.content)["query"]
    assert "findStudios" in json.loads(calls[4].request.content)["query"]
    assert "imageUpdate" in json.loads(calls[5].request.content)["query"]


@pytest.mark.asyncio
async def test_bundle_permission_inheritance(
    respx_stash_processor,
    mock_performer,
    test_account,
    session,
):
    """Test that media items inherit bundle permissions.

    Note: permissions is just data, not a database field in AccountMediaBundle.
    This test verifies that _process_bundle_media properly handles permissions.
    """
    # Arrange
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory
    from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory
    from tests.fixtures.metadata.metadata_fixtures import ACCOUNT_MEDIA_BUNDLE_ID_BASE

    # Create bundle fresh in the async session
    test_media_bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111224,
        accountId=test_account.id,
    )
    session.add(test_media_bundle)
    await session.commit()

    # Create a post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session.add(test_post)
    await session.commit()

    # Create REAL media items using factories
    media_items = []
    for i in range(2):
        # Create Media
        media = MediaFactory.build(
            id=300000 + i,
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
            is_downloaded=True,
        )
        session.add(media)
        await session.commit()

        # Create MediaLocation
        media_location = MediaLocationFactory.build(
            mediaId=media.id,
            locationId=300 + i,
            location=f"https://example.com/permission_media_{i}.jpg",
        )
        session.add(media_location)
        await session.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        session.add(account_media)
        await session.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    for i, account_media in enumerate(media_items):
        await session.execute(
            account_media_bundle_media.insert().values(
                bundle_id=test_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    await session.commit()

    # Refresh bundle to load relationships
    await session.refresh(test_media_bundle, attribute_names=["accountMedia"])

    # Eagerly load media and preview relationships
    for account_media in media_items:
        await session.refresh(account_media, attribute_names=["media", "preview"])

    # Mock Stash GraphQL HTTP responses
    # 2 images: findImages, findScenes, findPerformers, 2x findStudios, 2x imageUpdate

    images_data = []
    for i, account_media in enumerate(media_items):
        image_data = create_image_dict(
            id=f"image_{300000 + i}",
            title=f"Permission Image {i}",
            visual_files=[
                {
                    "id": f"file_{300000 + i}",
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

    # findPerformers call 1: by name (not found)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # findPerformers call 2: by alias (found)
    performer_dict = create_performer_dict(
        id=mock_performer.id,
        name=mock_performer.name,
    )
    find_performers_data = create_find_performers_result(
        count=1, performers=[performer_dict]
    )

    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Build response sequence (no findScenes - bundle has only images)
    responses = [
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
        # First image processing
        httpx.Response(
            200, json=create_graphql_response("findPerformers", empty_performers_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", creator_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[0])
        ),
        # Second image processing (performer/studio lookups not cached)
        httpx.Response(
            200, json=create_graphql_response("findPerformers", empty_performers_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", creator_studio_result)
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
    await respx_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify bundle was processed
    assert len(result["images"]) == 2

    # Verify items are in correct order by manually querying the junction table
    stmt = (
        account_media_bundle_media.select()
        .where(account_media_bundle_media.c.bundle_id == test_media_bundle.id)
        .order_by(account_media_bundle_media.c.pos)
    )
    result_rows = await session.execute(stmt)
    bundle_media_ids = [row.media_id for row in result_rows.all()]
    assert len(bundle_media_ids) == 2
    assert bundle_media_ids == [m.id for m in media_items]

    # Verify GraphQL call sequence (permanent assertion)
    # 11 calls: 1 findImages + (2 findPerformers + 2 findStudios + 1 imageUpdate) x 2 images
    assert len(graphql_route.calls) == 11, "Expected exactly 11 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]
    # First image
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]  # By name
    assert "findPerformers" in json.loads(calls[2].request.content)["query"]  # By alias
    assert "findStudios" in json.loads(calls[3].request.content)["query"]  # Fansly
    assert "findStudios" in json.loads(calls[4].request.content)["query"]  # Creator
    assert "imageUpdate" in json.loads(calls[5].request.content)["query"]
    # Second image
    assert "findPerformers" in json.loads(calls[6].request.content)["query"]  # By name
    assert "findPerformers" in json.loads(calls[7].request.content)["query"]  # By alias
    assert "findStudios" in json.loads(calls[8].request.content)["query"]  # Fansly
    assert "findStudios" in json.loads(calls[9].request.content)["query"]  # Creator
    assert "imageUpdate" in json.loads(calls[10].request.content)["query"]
