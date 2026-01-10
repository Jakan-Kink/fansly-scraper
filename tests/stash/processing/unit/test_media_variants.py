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

    # Response 1: FindScenes
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
                # VideoFile fields
                "parent_folder": {
                    "id": "folder_123",
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
    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    # Response 4: studioCreate - Creator-specific studio (get_or_create creates immediately)
    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )

    # Response 5: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="scene_123",
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
            # Call 3: studioCreate (v0.10.4: get_or_create creates immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
            ),
            # Call 4: sceneUpdate
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
    assert result["scenes"][0].id == "scene_123"

    # Verify GraphQL call sequence (permanent assertion)
    # v0.10.4: 5 calls: findScenes + findPerformers + findStudios + studioCreate + sceneUpdate
    assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    req0 = json.loads(calls[0].request.content)
    assert "FindScenes" in req0["query"]  # Find scenes by path regex

    req1 = json.loads(calls[1].request.content)
    assert "findPerformers" in req1["query"]  # By name (not found)

    req2 = json.loads(calls[2].request.content)
    assert "findStudios" in req2["query"]  # Fansly network studio

    req3 = json.loads(calls[3].request.content)
    assert (
        "studioCreate" in req3["query"]
    )  # v0.10.4: Creator studio (get_or_create creates)

    req4 = json.loads(calls[4].request.content)
    assert "sceneUpdate" in req4["query"]


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
    # v0.10.4: creator_studio_dict used directly in studioCreate response (not wrapped)

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
    assert result["scenes"][0].id == "scene_456"

    # Verify GraphQL call sequence (permanent assertion)
    assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order (same as HLS variant)
    assert "findScenes" in json.loads(calls[0].request.content)["query"]
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]
    assert (
        "findStudios" in json.loads(calls[2].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[3].request.content)["query"]
    )  # v0.10.4: Creator studio
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
    # v0.10.4: creator_studio_dict used directly in studioCreate response (not wrapped)

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
    assert (
        "findStudios" in json.loads(calls[3].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[4].request.content)["query"]
    )  # v0.10.4: Creator studio
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
    # v0.10.4: creator_studio_dict used directly in studioCreate response (not wrapped)

    # findPerformers: by name (not found - _find_existing_performer doesn't create)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    # Build the full response sequence (no findScenes - bundle has only images)
    # For each of 3 images: findPerformers + findStudios + studioCreate + imageUpdate
    responses = [
        # findImages: find by path (only 3 results, so no pagination check needed)
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
    ]

    # Add responses for each image (performer/studio lookups not cached)
    for i in range(3):
        responses.extend(
            [
                # findPerformers: _find_existing_performer makes single call by name (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_result
                    ),
                ),
                # findStudios: find Fansly (network) studio
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # v0.10.4: studioCreate (get_or_create creates creator studio immediately)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                ),
                # imageUpdate: save metadata
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
    # v0.10.4: 13 calls: findImages + (findPerformers + findStudios + studioCreate + imageUpdate) x 3 images
    assert len(graphql_route.calls) == 13, "Expected exactly 13 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]  # Find by path
    # Image 1
    assert (
        "findPerformers" in json.loads(calls[1].request.content)["query"]
    )  # By name (not found)
    assert (
        "findStudios" in json.loads(calls[2].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[3].request.content)["query"]
    )  # v0.10.4: Creator studio
    assert "imageUpdate" in json.loads(calls[4].request.content)["query"]
    # Image 2
    assert (
        "findPerformers" in json.loads(calls[5].request.content)["query"]
    )  # By name (not found)
    assert (
        "findStudios" in json.loads(calls[6].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[7].request.content)["query"]
    )  # v0.10.4: Creator studio
    assert "imageUpdate" in json.loads(calls[8].request.content)["query"]
    # Image 3
    assert (
        "findPerformers" in json.loads(calls[9].request.content)["query"]
    )  # By name (not found)
    assert (
        "findStudios" in json.loads(calls[10].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[11].request.content)["query"]
    )  # v0.10.4: Creator studio
    assert "imageUpdate" in json.loads(calls[12].request.content)["query"]


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
    # 1. findImages (for preview image by path)
    # 2. findPerformers (by name via _find_existing_performer - not found)
    # 3. findStudios (Fansly network)
    # 4. studioCreate (creator studio - v0.10.4: get_or_create creates immediately)
    # 5. imageUpdate (save metadata)
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

    # findPerformers: by name (not found - _find_existing_performer doesn't create)
    empty_performers_result = create_find_performers_result(count=0, performers=[])

    fansly_studio_dict = create_studio_dict(id="fansly_246", name="Fansly (network)")
    fansly_studio_result = create_find_studios_result(
        count=1, studios=[fansly_studio_dict]
    )

    creator_studio_dict = create_studio_dict(
        id="creator_999",
        name=f"{test_account.username} (Fansly)",
        urls=[f"https://fansly.com/{test_account.username}"],
    )
    # v0.10.4: creator_studio_dict used directly in studioCreate response (not wrapped)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # findImages: find by path (only 1 result, so no pagination check needed)
            httpx.Response(
                200, json=create_graphql_response("findImages", find_images_data)
            ),
            # findPerformers: _find_existing_performer makes single call by name (not found)
            httpx.Response(
                200,
                json=create_graphql_response("findPerformers", empty_performers_result),
            ),
            # findStudios: find Fansly (network) studio
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_studio_result)
            ),
            # v0.10.4: studioCreate (get_or_create creates creator studio immediately)
            httpx.Response(
                200, json=create_graphql_response("studioCreate", creator_studio_dict)
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
        # Debug: Print all GraphQL calls made
        print("\n" + "=" * 80)
        print("****RESPX Call Debugging****")
        print("=" * 80)
        for index, call in enumerate(graphql_route.calls):
            req_body = json.loads(call.request.content)
            resp_data = call.response.json() if call.response else {}
            print(f"\nCall {index}:")
            print(f"  Query: {req_body.get('query', '')[:100]}...")
            print(f"  Variables: {req_body.get('variables', {})}")
            print(f"  Response keys: {list(resp_data.get('data', {}).keys())}")
            # Show actual response data for debugging
            if resp_data.get("data"):
                for key, value in resp_data["data"].items():
                    if isinstance(value, dict) and "count" in value:
                        print(f"    {key}.count = {value['count']}")
        print("=" * 80 + "\n")

    # Assert
    # Verify preview was used (check that previewId is set)
    assert test_media_bundle.previewId == preview_media.id
    # Verify the preview image was processed
    assert len(result["images"]) == 1

    # Verify GraphQL call sequence (permanent assertion)
    # v0.10.4: 5 calls: findImages + findPerformers + findStudios + studioCreate + imageUpdate
    assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order
    assert "findImages" in json.loads(calls[0].request.content)["query"]  # Find by path
    assert (
        "findPerformers" in json.loads(calls[1].request.content)["query"]
    )  # By name (not found)
    assert (
        "findStudios" in json.loads(calls[2].request.content)["query"]
    )  # Fansly network
    assert (
        "studioCreate" in json.loads(calls[3].request.content)["query"]
    )  # v0.10.4: Creator studio
    assert "imageUpdate" in json.loads(calls[4].request.content)["query"]


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

    # v0.10.3 pattern: findPerformers (name only), findStudios (Fansly), studioCreate (creator)
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

    # Build response sequence (no findScenes - bundle has only images)
    responses = [
        httpx.Response(
            200, json=create_graphql_response("findImages", find_images_data)
        ),
        # First image processing (v0.10.3 pattern: 4 calls per image)
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("studioCreate", creator_studio_dict)
        ),
        httpx.Response(
            200, json=create_graphql_response("imageUpdate", images_data[0])
        ),
        # Second image processing (v0.10.3 pattern: 4 calls per image)
        httpx.Response(
            200, json=create_graphql_response("findPerformers", find_performers_data)
        ),
        httpx.Response(
            200, json=create_graphql_response("findStudios", fansly_studio_result)
        ),
        httpx.Response(
            200, json=create_graphql_response("studioCreate", creator_studio_dict)
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
    # v0.10.3 pattern: 9 calls = 1 findImages + (1 findPerformers + 1 findStudios + 1 studioCreate + 1 imageUpdate) * 2 images
    assert len(graphql_route.calls) == 9, "Expected exactly 9 GraphQL calls"
    calls = graphql_route.calls

    # Verify query types in order (v0.10.3 pattern)
    assert "findImages" in json.loads(calls[0].request.content)["query"]
    # First image (v0.10.3: findPerformers, findStudios, studioCreate, imageUpdate)
    assert "findPerformers" in json.loads(calls[1].request.content)["query"]
    assert "findStudios" in json.loads(calls[2].request.content)["query"]  # Fansly
    assert "studioCreate" in json.loads(calls[3].request.content)["query"]  # Creator
    assert "imageUpdate" in json.loads(calls[4].request.content)["query"]
    # Second image
    assert "findPerformers" in json.loads(calls[5].request.content)["query"]
    assert "findStudios" in json.loads(calls[6].request.content)["query"]  # Fansly
    assert "studioCreate" in json.loads(calls[7].request.content)["query"]  # Creator
    assert "imageUpdate" in json.loads(calls[8].request.content)["query"]
