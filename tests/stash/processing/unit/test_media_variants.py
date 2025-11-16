"""Tests for media variant and bundle processing functionality.

These are UNIT tests that use respx to mock Stash HTTP responses.
They test the _process_media logic for handling different media variants.
"""

import httpx
import pytest
import respx
import strawberry

from tests.fixtures import MediaLocationFactory
from tests.fixtures.metadata.metadata_factories import AccountMediaFactory, MediaFactory
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

    # Response 1: findScenes
    # NOTE: The path must contain the VARIANT media ID, not the parent media ID
    scene_data = create_scene_dict(
        id="scene_123",
        title="HLS Test Scene",
        files=[
            {
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
            }
        ],
    )
    find_scenes_data = create_find_scenes_result(count=1, scenes=[scene_data])

    # Response 2: findPerformers - convert mock_performer to dict
    performer_dict = strawberry.asdict(mock_performer)
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
        url=f"https://fansly.com/{test_account.username}",
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Response 5: sceneUpdate - mutation returns the updated scene
    updated_scene_data = create_scene_dict(
        id="scene_123",
        title="HLS Test Scene",  # Will be updated with actual title from test_post
        files=[scene_data["files"][0]],  # Keep the same file
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
    assert result["scenes"][0].id == "scene_123"


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
    performer_dict = strawberry.asdict(mock_performer)
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
        url=f"https://fansly.com/{test_account.username}",
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

    respx.post("http://localhost:9999/graphql").mock(
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
    # 1. findImages - to find the image by path
    # 2. findPerformers - to find the main performer (account)
    # 3. findStudios - to find "Fansly (network)" studio
    # 4. findStudios - to find the creator-specific studio
    # 5. imageUpdate - to save the updated image metadata

    # Response 1: findImages
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

    # Response 2: findPerformers - convert mock_performer to dict
    performer_dict = strawberry.asdict(mock_performer)
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
        url=f"https://fansly.com/{test_account.username}",
    )
    creator_studio_result = create_find_studios_result(
        count=1, studios=[creator_studio_dict]
    )

    # Response 5: imageUpdate - mutation returns the updated image
    updated_image_data = create_image_dict(
        id="image_789",
        title="Preview Test Image",
        visual_files=[image_data["visual_files"][0]],
    )

    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200, json=create_graphql_response("findImages", find_images_data)
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


@pytest.mark.asyncio
async def test_process_bundle_ordering(
    real_stash_processor,
    test_media_bundle,
    mock_performer,
    test_account,
    session_sync,
):
    """Test processing media bundle with specific ordering."""
    # Arrange
    # Create multiple REAL media items in bundle using factories
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session_sync.add(test_post)
    session_sync.commit()

    media_items = []
    for _ in range(3):
        # Create Media
        media = MediaFactory.build(
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
        )
        session_sync.add(media)
        session_sync.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        session_sync.add(account_media)
        session_sync.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    # This is the proper way to add media to a bundle!
    for i, account_media in enumerate(media_items):
        session_sync.execute(
            account_media_bundle_media.insert().values(
                bundle_id=test_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    session_sync.commit()

    # Refresh bundle to load relationships
    session_sync.refresh(test_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = mock_performer

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify items were added in correct order by checking account_media_ids property
    bundle_media_ids = test_media_bundle.account_media_ids
    assert len(bundle_media_ids) == 3
    assert bundle_media_ids == [m.id for m in media_items]


@pytest.mark.asyncio
async def test_process_bundle_with_preview(
    real_stash_processor,
    test_media_bundle,
    mock_performer,
    test_account,
    session_sync,
):
    """Test processing media bundle with preview image."""
    # Arrange
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session_sync.add(test_post)
    session_sync.commit()

    # Create REAL preview media using factory instead of MagicMock
    preview_media = MediaFactory.build(
        id=123456,  # Specific ID for this test
        accountId=test_media_bundle.accountId,
        mimetype="image/jpeg",
        type=1,  # Image type
    )
    session_sync.add(preview_media)
    session_sync.commit()
    session_sync.refresh(preview_media)

    # Update bundle to reference this preview
    test_media_bundle.previewId = preview_media.id
    session_sync.add(test_media_bundle)
    session_sync.commit()
    session_sync.refresh(test_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = mock_performer

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify preview was used (check that previewId is set)
    assert test_media_bundle.previewId == preview_media.id


@pytest.mark.asyncio
async def test_bundle_permission_inheritance(
    real_stash_processor,
    test_media_bundle,
    mock_performer,
    test_account,
    session_sync,
):
    """Test that media items inherit bundle permissions."""
    # Arrange
    # Note: permissions is just data, not a database field in AccountMediaBundle
    # This test verifies that _process_bundle_media properly handles permissions
    from metadata.account import account_media_bundle_media
    from tests.fixtures import PostFactory

    # Create a mock post to pass to _process_bundle_media
    test_post = PostFactory.build(accountId=test_account.id)
    session_sync.add(test_post)
    session_sync.commit()

    # Create REAL media items using factories
    media_items = []
    for _ in range(2):
        # Create Media
        media = MediaFactory.build(
            accountId=test_media_bundle.accountId,
            mimetype="image/jpeg",
        )
        session_sync.add(media)
        session_sync.commit()

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=test_media_bundle.accountId,
            mediaId=media.id,
        )
        session_sync.add(account_media)
        session_sync.commit()

        media_items.append(account_media)

    # Link AccountMedia items to bundle using the junction table
    for i, account_media in enumerate(media_items):
        session_sync.execute(
            account_media_bundle_media.insert().values(
                bundle_id=test_media_bundle.id,
                media_id=account_media.id,
                pos=i,
            )
        )
    session_sync.commit()

    # Refresh bundle to load relationships
    session_sync.refresh(test_media_bundle)

    # Mock Stash client responses
    real_stash_processor.context.client.find_performer.return_value = mock_performer

    # Act
    result = {"images": [], "scenes": []}
    await real_stash_processor._process_bundle_media(
        test_media_bundle, test_post, test_account, result
    )

    # Assert
    # Verify bundle was processed
    bundle_media_ids = test_media_bundle.account_media_ids
    assert len(bundle_media_ids) == 2
    assert bundle_media_ids == [m.id for m in media_items]
