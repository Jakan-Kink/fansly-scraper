"""Integration tests for media processing in StashProcessing.

This module tests media processing using real database fixtures,
factory-based test data, and TRUE integration with Stash instance.
These tests make real GraphQL calls to Stash (find existing images,
create studios/performers, etc.).
"""

import random
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fileio.fnmanip import extract_media_id
from metadata import (
    ContentType,
)
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


class TestMediaProcessingIntegration:
    """Integration tests for media processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_media_integration(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
    ):
        """Test media processing through attachment workflow with real Stash integration.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        - Finds existing images from the 202 images in test Stash
        - Creates real studio for the creator
        - Processes real attachment through the full pipeline
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Find a random image from Stash (randomize to avoid always testing the same image)
            random_page = random.randint(  # noqa: S311
                1, 202
            )  # Pick a random image from the 202 available

            find_result = await real_stash_processor.context.client.find_images(
                filter_={"per_page": 1, "page": random_page}
            )

            if not find_result or find_result.count == 0:
                pytest.skip("No images found in Stash - cannot test media processing")

            # Get the real image and extract its file path
            # NOTE: Using Pydantic models from stash-graphql-client
            # Results are properly deserialized to Pydantic objects (no need to reconstruct)
            test_image = find_result.images[0]

            # Extract the file path from visual_files
            if not test_image.visual_files or len(test_image.visual_files) == 0:
                pytest.skip("Test image has no visual files - cannot test")

            # Get the first visual file
            visual_file = test_image.visual_files[0]
            image_file_path = visual_file.path

            # Extract media ID and date from the filename
            # Filenames have format like "2023-02-01_at_19-19_UTC_id_477247997156012032.jpg"
            # extract_media_id() looks for "_id_(\d+)" pattern
            file_path = Path(image_file_path)
            media_id = extract_media_id(file_path.name)

            if media_id is None:
                pytest.skip(
                    f"Could not extract media ID from filename: {file_path.name}"
                )

            # Extract date from filename (format: YYYY-MM-DD at start of filename)
            # This ensures the Post has an earlier date than the existing Stash image
            date_match = re.match(r"(\d{4}-\d{2}-\d{2})", file_path.name)
            if date_match:
                file_date_str = date_match.group(1)
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
            else:
                # Fallback to a known earlier date if pattern doesn't match
                file_date = datetime(2023, 1, 1, tzinfo=UTC)

            # Create a real account
            account = AccountFactory.build(username="media_user")
            await entity_store.save(account)

            # Create a real post with date matching the image file
            # This ensures _update_stash_metadata won't skip the update
            post = PostFactory.build(
                accountId=account.id,
                content="Test post",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            # Create real media with ID extracted from filename
            # The processing code searches for files where str(media.id) appears in the path
            media = MediaFactory.build(
                id=media_id,  # Use extracted ID so search finds it
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,  # Not used by processing, but keep for reference
            )
            await entity_store.save(media)

            # Create AccountMedia as intermediary layer
            account_media = AccountMediaFactory.build(
                accountId=account.id, mediaId=media.id
            )
            await entity_store.save(account_media)

            # Create attachment pointing to AccountMedia
            attachment = AttachmentFactory.build(
                postId=post.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=account_media.id,
            )
            await entity_store.save(attachment)

            # Wire up in-memory relationships for traversal
            post.attachments = [attachment]

            # Clear store cache so process_creator_attachment makes fresh
            # GraphQL calls instead of serving from cache-first path
            real_stash_processor.context.store.invalidate_all()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    # Make real GraphQL calls to Stash
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=attachment,
                        item=post,
                        account=account,
                    )
                finally:
                    dump_graphql_calls(calls, "test_process_media_integration")

        # Verify expected GraphQL calls
        # The image lookup may use cache-first path (store.filter_and_populate)
        # which doesn't generate a findImages GraphQL call. Expected calls:
        # - findImages (only if cache miss on path search)
        # - findPerformers - search by name/alias
        # - findStudios - find network and creator studios
        # - studioCreate - create creator studio if not found (first run only)
        # - imageUpdate/UpdateImage - save metadata
        assert len(calls) >= 1, f"Expected at least 1 GraphQL call, got {len(calls)}"

        # imageUpdate should be present when image was found and processed
        update_calls = [
            c
            for c in calls
            if "imageUpdate" in c.get("query", "")
            or "UpdateImage" in c.get("query", "")
        ]
        assert len(update_calls) >= 1, (
            f"Expected imageUpdate call, got: "
            f"{[c.get('query', '')[:40] for c in calls]}"
        )

        # Track created studios for cleanup
        for call in calls:
            if "studioCreate" in call.get("query", ""):
                created_studio_id = call["result"]["studioCreate"]["id"]
                cleanup["studios"].append(created_studio_id)

        # Verify results structure
        assert isinstance(result, dict)
        assert "images" in result
        assert "scenes" in result
        # Should have processed at least one image
        assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_bundle_media_integration(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
        faker,
        enable_scene_creation,
    ):
        """Test bundle media processing through attachment workflow with real Stash integration.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        - Randomizes bundle composition (0-3 images, 0-3 scenes, but always at least 1 total)
        - Finds existing images from Stash OR creates scenes in Stash
        - Creates real studio for the creator
        - Processes bundle with varying media types through full pipeline
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Randomize bundle composition using Faker
            # Always have at least 1 media item (either image or scene)
            num_images = faker.random_int(min=0, max=3)
            num_scenes = faker.random_int(min=0, max=3)

            # Ensure we have at least one media item
            if num_images == 0 and num_scenes == 0:
                # Randomly choose to add either an image or a scene
                if faker.pybool():
                    num_images = 1
                else:
                    num_scenes = 1

            # Create a real account
            account = AccountFactory.build(username="bundle_user")
            await entity_store.save(account)

            # Will store earliest date from all media for the Post
            earliest_date = None
            bundle_media_list = []

            # Collect images if needed
            for _i in range(num_images):
                random_page = random.randint(1, 202)  # noqa: S311

                image_result = await real_stash_processor.context.client.find_images(
                    filter_={"per_page": 1, "page": random_page}
                )

                if not image_result or image_result.count == 0:
                    pytest.skip(
                        "No images found in Stash - cannot test bundle processing"
                    )

                # Pydantic model already deserialized from GraphQL
                test_image = image_result.images[0]

                if not test_image.visual_files or len(test_image.visual_files) == 0:
                    continue  # Skip this image, try to continue with others

                visual_file = test_image.visual_files[0]
                image_file_path = visual_file.path

                image_path = Path(image_file_path)
                image_media_id = extract_media_id(image_path.name)

                if image_media_id is None:
                    continue  # Skip if can't extract ID

                # Extract date from filename
                date_match = re.match(r"(\d{4}-\d{2}-\d{2})", image_path.name)
                if date_match:
                    file_date_str = date_match.group(1)
                    file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(
                        tzinfo=UTC
                    )
                    if earliest_date is None or file_date < earliest_date:
                        earliest_date = file_date

                bundle_media_list.append(
                    {
                        "type": "image",
                        "id": image_media_id,
                        "path": image_file_path,
                        "mimetype": "image/jpeg",
                        "media_type": 1,
                    }
                )

            # Use existing scenes from Stash (simpler and more portable than creating new ones)
            for _j in range(num_scenes):
                # Find a random existing scene to get a video file from it
                random_scene_page = random.randint(  # noqa: S311
                    1, 50
                )  # Pick from available scenes

                scene_result = await real_stash_processor.context.client.find_scenes(
                    filter_={"per_page": 1, "page": random_scene_page}
                )

                if scene_result.count == 0 or not scene_result.scenes:
                    # No scenes at this page, skip
                    continue

                # Get the scene from the result (Pydantic model already deserialized)
                existing_scene = scene_result.scenes[0]

                # Scenes must have files
                if not existing_scene.files or len(existing_scene.files) == 0:
                    continue

                # Get the video file path from existing scene (no need to create new scene)
                video_file = existing_scene.files[0]
                scene_file_path = video_file.path

                # Extract media ID from filename
                scene_media_id = extract_media_id(Path(scene_file_path).name)
                if scene_media_id is None:
                    continue

                # Extract date from filename
                scene_filename = Path(scene_file_path).name
                scene_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", scene_filename)
                scene_date = (
                    datetime.strptime(scene_date_match.group(1), "%Y-%m-%d").replace(
                        tzinfo=UTC
                    )
                    if scene_date_match
                    else datetime.now(UTC)
                )

                if earliest_date is None or scene_date < earliest_date:
                    earliest_date = scene_date

                bundle_media_list.append(
                    {
                        "type": "scene",
                        "id": scene_media_id,
                        "path": scene_file_path,
                        "mimetype": "video/mp4",
                        "media_type": 2,
                    }
                )

            # Ensure we have at least one media item after collection
            if len(bundle_media_list) == 0:
                pytest.skip(
                    "Could not collect any valid media for bundle - test cannot proceed"
                )

            # Use earliest date or fallback
            if earliest_date is None:
                earliest_date = datetime(2023, 1, 1, tzinfo=UTC)

            # Create a real post with earliest date
            post = PostFactory.build(
                accountId=account.id,
                content="Bundle post",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            # Create a real bundle
            bundle = AccountMediaBundleFactory.build(accountId=account.id)

            # Create Media and AccountMedia entries for all items in bundle
            account_media_list = []
            for _idx, media_info in enumerate(bundle_media_list):
                media = MediaFactory.build(
                    id=media_info["id"],
                    accountId=account.id,
                    mimetype=media_info["mimetype"],
                    type=media_info["media_type"],
                    is_downloaded=True,
                    stash_id=None,
                    local_filename=media_info["path"],
                )
                await entity_store.save(media)

                account_media = AccountMediaFactory.build(
                    accountId=account.id, mediaId=media.id
                )
                await entity_store.save(account_media)
                account_media_list.append(account_media)

            # Wire bundle's accountMedia relationship in-memory and save
            bundle.accountMedia = account_media_list
            await entity_store.save(bundle)

            # Sync the junction table for bundle <-> accountMedia
            await entity_store.sync_junction(
                "account_media_bundle_media",
                "bundle_id",
                bundle.id,
                [
                    {"media_id": am.id, "pos": idx}
                    for idx, am in enumerate(account_media_list)
                ],
            )

            # Create attachment pointing to bundle
            attachment = AttachmentFactory.build(
                postId=post.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                contentId=bundle.id,
            )
            await entity_store.save(attachment)

            # Wire up post.attachments for traversal
            post.attachments = [attachment]

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=attachment,
                        item=post,
                        account=account,
                    )
                finally:
                    dump_graphql_calls(calls, "test_process_bundle_media_integration")

            # Verify GraphQL calls based on randomized bundle composition
            # Cache-first pattern: findImages/findScenes may be served from
            # store cache (no GraphQL call). Performers and studios may also
            # be cached from prior tests. The update calls are always made.
            num_bundle_images = sum(
                1 for m in bundle_media_list if m["type"] == "image"
            )
            num_bundle_scenes = sum(
                1 for m in bundle_media_list if m["type"] == "scene"
            )
            total_media = num_bundle_images + num_bundle_scenes

            # At minimum, each found media item triggers an update call
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Count update calls
            image_update_calls = [
                c
                for c in calls
                if "imageUpdate" in c.get("query", "")
                or "UpdateImage" in c.get("query", "")
            ]
            scene_update_calls = [
                c
                for c in calls
                if "sceneUpdate" in c.get("query", "")
                or "UpdateScene" in c.get("query", "")
            ]
            studio_create_calls = [
                c for c in calls if "studioCreate" in c.get("query", "")
            ]

            # Each media item should have an update call
            if num_bundle_images > 0:
                assert len(image_update_calls) >= num_bundle_images, (
                    f"Should update {num_bundle_images} images, got {len(image_update_calls)}"
                )
            if num_bundle_scenes > 0:
                assert len(scene_update_calls) >= num_bundle_scenes, (
                    f"Should update {num_bundle_scenes} scenes, got {len(scene_update_calls)}"
                )

            # Track created studios for cleanup
            if len(studio_create_calls) > 0:
                for studio_call in studio_create_calls:
                    created_studio_id = studio_call["result"]["studioCreate"]["id"]
                    if created_studio_id not in cleanup["studios"]:
                        cleanup["studios"].append(created_studio_id)

            # Verify results structure
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result

            # Verify results match bundle composition
            if num_bundle_images > 0:
                assert len(result["images"]) >= num_bundle_images, (
                    f"Should have processed {num_bundle_images} images"
                )
            if num_bundle_scenes > 0:
                assert len(result["scenes"]) >= num_bundle_scenes, (
                    f"Should have processed {num_bundle_scenes} scenes"
                )

    @pytest.mark.asyncio
    async def test_process_creator_attachment_integration(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
    ):
        """Test process_creator_attachment method with single image attachment.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        - Finds random existing image from Stash (out of 202 available)
        - Creates real studio for the creator
        - Processes single attachment through full pipeline
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Find a random image from Stash (randomize to avoid always testing the same image)
            random_page = random.randint(  # noqa: S311
                1, 202
            )  # Pick a random image from the 202 available

            find_result = await real_stash_processor.context.client.find_images(
                filter_={"per_page": 1, "page": random_page}
            )

            if not find_result or find_result.count == 0:
                pytest.skip(
                    "No images found in Stash - cannot test attachment processing"
                )

            # Get the real image (Pydantic model already deserialized)
            test_image = find_result.images[0]

            if not test_image.visual_files or len(test_image.visual_files) == 0:
                pytest.skip("Test image has no visual files - cannot test")

            visual_file = test_image.visual_files[0]
            image_file_path = visual_file.path

            # Extract media ID and date from filename
            file_path = Path(image_file_path)
            media_id = extract_media_id(file_path.name)

            if media_id is None:
                pytest.skip(
                    f"Could not extract media ID from filename: {file_path.name}"
                )

            # Extract date from filename
            date_match = re.match(r"(\d{4}-\d{2}-\d{2})", file_path.name)
            if date_match:
                file_date_str = date_match.group(1)
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
            else:
                file_date = datetime(2023, 1, 1, tzinfo=UTC)

            # Create a real account
            account = AccountFactory.build(username="attachment_user")
            await entity_store.save(account)

            # Create a real post with date matching the file
            post = PostFactory.build(
                accountId=account.id,
                content="Attachment post",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            # Create real media with extracted ID
            media = MediaFactory.build(
                id=media_id,
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,
            )
            await entity_store.save(media)

            # Create real AccountMedia to link media to account
            account_media = AccountMediaFactory.build(
                accountId=account.id, mediaId=media.id
            )
            await entity_store.save(account_media)

            # Create real attachment with proper ContentType
            attachment = AttachmentFactory.build(
                contentId=account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                postId=post.id,
            )
            await entity_store.save(attachment)

            # Wire up post.attachments for traversal
            post.attachments = [attachment]

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=attachment,
                        item=post,
                        account=account,
                    )
                finally:
                    dump_graphql_calls(
                        calls, "test_process_creator_attachment_integration"
                    )

            # Cache-first: findImages may be served from store cache
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Track created studios for cleanup
            for call in calls:
                if "studioCreate" in call.get("query", ""):
                    created_studio_id = call["result"]["studioCreate"]["id"]
                    cleanup["studios"].append(created_studio_id)

            # Verify an update call was made
            update_calls = [
                c
                for c in calls
                if "imageUpdate" in c.get("query", "")
                or "UpdateImage" in c.get("query", "")
            ]
            assert len(update_calls) >= 1, "Expected imageUpdate call"

            # Verify results structure
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
    ):
        """Test process_creator_attachment with bundle attachment.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        - Finds random existing image from Stash
        - Creates bundle with the image
        - Processes bundle attachment through full pipeline
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Find a random image from Stash
            random_page = random.randint(  # noqa: S311
                1, 202
            )  # Pick a random image from the 202 available

            find_result = await real_stash_processor.context.client.find_images(
                filter_={"per_page": 1, "page": random_page}
            )
            assert find_result.count > 0, "No images found in Stash"

            # Get the image from the result
            # NOTE: Using Pydantic models from stash-graphql-client
            # Results are properly deserialized to Pydantic objects (no need to reconstruct)
            stash_image = find_result.images[0]

            # Extract the file path from visual_files
            if not stash_image.visual_files or len(stash_image.visual_files) == 0:
                pytest.skip("Test image has no visual files - cannot test")

            # Get the first visual file
            image_file_path = stash_image.visual_files[0].path

            # Extract media ID from filename
            media_id = extract_media_id(Path(image_file_path).name)
            assert media_id is not None, "Could not extract media ID from filename"

            # Extract date from filename (format: YYYY-MM-DD)
            filename = Path(image_file_path).name
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
            file_date = (
                datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
                if date_match
                else datetime.now(UTC)
            )

            # Create a real account
            account = AccountFactory.build(username="bundle_attachment_test_user")
            await entity_store.save(account)

            # Create a real post with date matching the file
            post = PostFactory.build(
                accountId=account.id,
                content="Bundle attachment post",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            # Create real media with extracted ID
            media = MediaFactory.build(
                id=media_id,
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,
            )
            await entity_store.save(media)

            # Create real AccountMedia to link media to account
            account_media = AccountMediaFactory.build(
                accountId=account.id, mediaId=media.id
            )
            await entity_store.save(account_media)

            # Create real bundle with media
            bundle = AccountMediaBundleFactory.build(accountId=account.id)
            bundle.accountMedia = [account_media]
            await entity_store.save(bundle)

            # Sync the junction table for bundle <-> accountMedia
            await entity_store.sync_junction(
                "account_media_bundle_media",
                "bundle_id",
                bundle.id,
                [{"media_id": account_media.id, "pos": 0}],
            )

            # Create attachment pointing to bundle
            attachment = AttachmentFactory.build(
                contentId=bundle.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                postId=post.id,
            )
            await entity_store.save(attachment)

            # Wire up post.attachments for traversal
            post.attachments = [attachment]

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=attachment,
                        item=post,
                        account=account,
                    )
                finally:
                    dump_graphql_calls(
                        calls, "test_process_creator_attachment_with_bundle"
                    )

            # Cache-first: findImages may be served from store cache
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Track created studios for cleanup
            for call in calls:
                if "studioCreate" in call.get("query", ""):
                    created_studio_id = call["result"]["studioCreate"]["id"]
                    cleanup["studios"].append(created_studio_id)

            # Verify an update call was made
            update_calls = [
                c
                for c in calls
                if "imageUpdate" in c.get("query", "")
                or "UpdateImage" in c.get("query", "")
            ]
            assert len(update_calls) >= 1, "Expected imageUpdate call"

            # Verify bundle was processed and results collected
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
    ):
        """Test process_creator_attachment with aggregated post attachment.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        - Creates parent post with AGGREGATED_POSTS attachment
        - Creates aggregated post with real image attachment
        - Processes aggregated post recursively through full pipeline
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Find a random image from Stash for the aggregated post
            random_page = random.randint(  # noqa: S311
                1, 202
            )  # Pick a random image from the 202 available

            find_result = await real_stash_processor.context.client.find_images(
                filter_={"per_page": 1, "page": random_page}
            )
            assert find_result.count > 0, "No images found in Stash"

            # Get the image from the result
            # NOTE: Using Pydantic models from stash-graphql-client
            # Results are properly deserialized to Pydantic objects (no need to reconstruct)
            stash_image = find_result.images[0]

            # Extract the file path from visual_files
            if not stash_image.visual_files or len(stash_image.visual_files) == 0:
                pytest.skip("Test image has no visual files - cannot test")

            # Get the first visual file
            image_file_path = stash_image.visual_files[0].path

            # Extract media ID from filename
            media_id = extract_media_id(Path(image_file_path).name)
            assert media_id is not None, "Could not extract media ID from filename"

            # Extract date from filename (format: YYYY-MM-DD)
            filename = Path(image_file_path).name
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
            file_date = (
                datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
                if date_match
                else datetime.now(UTC)
            )

            # Create account
            account = AccountFactory.build(username="aggregated_post_test_user")
            await entity_store.save(account)

            # Create parent post
            parent_post = PostFactory.build(
                accountId=account.id, content="Parent post", createdAt=file_date
            )
            await entity_store.save(parent_post)

            # Create aggregated post
            agg_post = PostFactory.build(
                accountId=account.id,
                content="Aggregated post",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(agg_post)

            # Create media
            media = MediaFactory.build(
                id=media_id,
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,
            )
            await entity_store.save(media)

            # Create AccountMedia
            account_media = AccountMediaFactory.build(
                accountId=account.id, mediaId=media.id
            )
            await entity_store.save(account_media)

            # Create attachment for aggregated post pointing to the media
            agg_attachment = AttachmentFactory.build(
                contentId=account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                postId=agg_post.id,
            )
            await entity_store.save(agg_attachment)

            # Wire up aggregated post's attachments
            agg_post.attachments = [agg_attachment]

            # Create attachment for parent post with AGGREGATED_POSTS type
            parent_attachment = AttachmentFactory.build(
                contentId=agg_post.id,
                contentType=ContentType.AGGREGATED_POSTS,
                postId=parent_post.id,
            )
            await entity_store.save(parent_attachment)

            # Wire up parent post's attachments
            parent_post.attachments = [parent_attachment]

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=parent_attachment,
                        item=parent_post,
                        account=account,
                    )
                finally:
                    dump_graphql_calls(
                        calls,
                        "test_process_creator_attachment_with_aggregated_post",
                    )

            # Cache-first: findImages may be served from store cache
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Track created studios for cleanup
            for call in calls:
                if "studioCreate" in call.get("query", ""):
                    created_studio_id = call["result"]["studioCreate"]["id"]
                    cleanup["studios"].append(created_studio_id)

            # Verify an update call was made
            update_calls = [
                c
                for c in calls
                if "imageUpdate" in c.get("query", "")
                or "UpdateImage" in c.get("query", "")
            ]
            assert len(update_calls) >= 1, "Expected imageUpdate call"

            # Verify results include aggregated content
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1
