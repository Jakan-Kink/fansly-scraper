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
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from fileio.fnmanip import extract_media_id
from metadata import Account
from metadata.account import (
    AccountMedia,
    AccountMediaBundle,
    account_media_bundle_media,
)
from metadata.attachment import Attachment, ContentType
from metadata.post import Post
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


class TestMediaProcessingIntegration:
    """Integration tests for media processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_media_integration(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
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
            account = AccountFactory(username="media_user")
            factory_session.commit()

            # Create a real post with date matching the image file
            # This ensures _update_stash_metadata won't skip the update
            post = PostFactory(
                accountId=account.id,
                content="Test post",
                createdAt=file_date,
            )
            factory_session.commit()

            # Create real media with ID extracted from filename
            # The processing code searches for files where str(media.id) appears in the path
            media = MediaFactory(
                id=media_id,  # Use extracted ID so search finds it
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,  # Not used by processing, but keep for reference
            )
            factory_session.commit()

            # Create AccountMedia as intermediary layer
            account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
            factory_session.commit()

            # Create attachment pointing to AccountMedia
            attachment = AttachmentFactory(
                postId=post.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=account_media.id,
            )
            factory_session.commit()

        # TRUE INTEGRATION: No mocks - all real GraphQL calls to Stash
        async with test_database_sync.async_session_scope() as async_session:
            from metadata.post import Post

            # Eager-load relationships (including bundle and aggregated_post to avoid lazy loading)
            result_query = await async_session.execute(
                select(Attachment)
                .where(Attachment.id == attachment.id)
                .options(
                    selectinload(Attachment.media).selectinload(AccountMedia.media),
                    selectinload(Attachment.bundle),
                    selectinload(Attachment.aggregated_post),
                )
            )
            async_attachment = result_query.unique().scalar_one()

            account_query = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_query.unique().scalar_one()

            # Query the post in async session to avoid lazy loading issues
            post_query = await async_session.execute(
                select(Post).where(Post.id == post.id)
            )
            async_post = post_query.unique().scalar_one()

            # Capture GraphQL calls for validation
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                # Make real GraphQL calls to Stash
                result = await real_stash_processor.process_creator_attachment(
                    attachment=async_attachment,
                    item=async_post,
                    account=async_account,
                    session=async_session,
                )

        # Verify expected GraphQL call sequence
        # Expected flow for simple image without mentions/hashtags:
        # 0. findImages - find by path
        # 1. findPerformers - search by name
        # 2. findPerformers - search by alias
        # 3. findStudios - find "Fansly (network)" parent studio
        # 4. findStudios - find "{creator} (Fansly)" creator studio
        # 5. studioCreate - create creator studio if not found (first run only)
        # 6. imageUpdate - save metadata via execute()

        # Exact count depends on whether studio already exists
        # Minimum 6 calls (without studioCreate), 7 with studioCreate
        assert len(calls) >= 6, f"Expected at least 6 GraphQL calls, got {len(calls)}"

        # Call 0: findImages
        assert "findImages" in calls[0]["query"]
        assert "image_filter" in calls[0]["variables"]
        assert "findImages" in calls[0]["result"]

        # Call 1: findPerformers (by name)
        assert "findPerformers" in calls[1]["query"]
        assert (
            calls[1]["variables"]["performer_filter"]["name"]["value"]
            == account.username
        )
        assert "findPerformers" in calls[1]["result"]

        # Call 2: findPerformers (by alias)
        assert "findPerformers" in calls[2]["query"]
        assert "findPerformers" in calls[2]["result"]
        assert (
            calls[2]["variables"]["performer_filter"]["aliases"]["value"]
            == account.username
        )

        # Call 3: findStudios for Fansly (network)
        assert "findStudios" in calls[3]["query"]
        assert calls[3]["variables"]["filter"]["q"] == "Fansly (network)"
        assert "findStudios" in calls[3]["result"]

        # Call 4: findStudios for creator studio
        assert "findStudios" in calls[4]["query"]
        assert calls[4]["variables"]["filter"]["q"] == f"{account.username} (Fansly)"
        assert "findStudios" in calls[4]["result"]

        # Call 5: Conditional - either studioCreate OR imageUpdate
        if "studioCreate" in calls[5]["query"]:
            # Studio was created
            assert (
                calls[5]["variables"]["input"]["name"] == f"{account.username} (Fansly)"
            )
            assert "studioCreate" in calls[5]["result"]
            created_studio_id = calls[5]["result"]["studioCreate"]["id"]
            cleanup["studios"].append(created_studio_id)

            # Call 6: imageUpdate (when studio was created)
            assert len(calls) == 7, (
                f"Expected 7 calls when studio created, got {len(calls)}"
            )
            assert "imageUpdate" in calls[6]["query"] or "execute" in str(calls[6])
            # Verify image update includes studio_id
            if "imageUpdate" in calls[6]["query"]:
                assert "input" in calls[6]["variables"]
                assert "studio_id" in calls[6]["variables"]["input"]
            assert "imageUpdate" in calls[6]["result"] or "data" in calls[6]["result"]
        else:
            # Studio already existed, went straight to imageUpdate
            assert len(calls) == 6, (
                f"Expected 6 calls when studio exists, got {len(calls)}"
            )
            assert "imageUpdate" in calls[5]["query"] or "execute" in str(calls[5])
            # Verify image update includes studio_id
            if "imageUpdate" in calls[5]["query"]:
                assert "input" in calls[5]["variables"]
                assert "studio_id" in calls[5]["variables"]["input"]
            assert "imageUpdate" in calls[5]["result"] or "data" in calls[5]["result"]

        # Verify results structure
        assert isinstance(result, dict)
        assert "images" in result
        assert "scenes" in result
        # Should have processed at least one image
        assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_bundle_media_integration(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
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
            account = AccountFactory(username="bundle_user")
            factory_session.commit()

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
            post = PostFactory(
                accountId=account.id,
                content="Bundle post",
                createdAt=earliest_date,
            )
            factory_session.commit()

            # Create a real bundle
            bundle = AccountMediaBundleFactory(accountId=account.id)
            factory_session.commit()

            # Create Media and AccountMedia entries for all items in bundle
            account_media_list = []
            for _idx, media_info in enumerate(bundle_media_list):
                media = MediaFactory(
                    id=media_info["id"],
                    accountId=account.id,
                    mimetype=media_info["mimetype"],
                    type=media_info["media_type"],
                    is_downloaded=True,
                    stash_id=None,
                    local_filename=media_info["path"],
                )
                factory_session.commit()

                account_media = AccountMediaFactory(
                    accountId=account.id, mediaId=media.id
                )
                factory_session.commit()
                account_media_list.append(account_media)

            # Link all AccountMedia to bundle via join table
            bundle_values = [
                {"bundle_id": bundle.id, "media_id": am.id, "pos": idx}
                for idx, am in enumerate(account_media_list)
            ]
            factory_session.execute(
                account_media_bundle_media.insert().values(bundle_values)
            )
            factory_session.commit()

            # Create attachment pointing to bundle
            attachment = AttachmentFactory(
                postId=post.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                contentId=bundle.id,
            )
            factory_session.commit()

            # Process in async session with proper relationship loading
            async with test_database_sync.async_session_scope() as async_session:
                # Eager-load all relationships
                result_query = await async_session.execute(
                    select(Attachment)
                    .where(Attachment.id == attachment.id)
                    .options(
                        selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.aggregated_post),
                    )
                )
                async_attachment = result_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                # Capture GraphQL calls for validation
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=async_attachment,
                        item=async_post,
                        account=async_account,
                        session=async_session,
                    )

            # Verify GraphQL call sequence based on randomized bundle composition
            # Count expected media types in bundle
            num_bundle_images = sum(
                1 for m in bundle_media_list if m["type"] == "image"
            )
            num_bundle_scenes = sum(
                1 for m in bundle_media_list if m["type"] == "scene"
            )

            # Expected minimum calls: findImages/Scenes per media + performer lookups + studio lookups + updates
            # Each media triggers: find call + 2x findPerformers + 2x findStudios + update
            # Plus possible studioCreate on first media
            expected_min_calls = (num_bundle_images + num_bundle_scenes) * 5
            assert len(calls) >= expected_min_calls, (
                f"Expected at least {expected_min_calls} GraphQL calls for "
                f"{num_bundle_images} images + {num_bundle_scenes} scenes, got {len(calls)}"
            )

            # Count actual call types (use filtering since we can't predict exact sequence with randomization)
            find_images_calls = [c for c in calls if "findImages" in c.get("query", "")]
            find_scenes_calls = [c for c in calls if "findScenes" in c.get("query", "")]
            find_performers_calls = [
                c for c in calls if "findPerformers" in c.get("query", "")
            ]
            find_studios_calls = [
                c for c in calls if "findStudios" in c.get("query", "")
            ]
            studio_create_calls = [
                c for c in calls if "studioCreate" in c.get("query", "")
            ]
            image_update_calls = [
                c for c in calls if "imageUpdate" in c.get("query", "")
            ]
            scene_update_calls = [
                c for c in calls if "sceneUpdate" in c.get("query", "")
            ]

            # Verify we made appropriate calls based on bundle composition
            # NOTE: Media deduplication may reduce findImages calls
            if num_bundle_images > 0:
                assert len(find_images_calls) >= 1, (
                    "Should call findImages at least once (deduplication may reduce calls)"
                )
                assert len(image_update_calls) >= num_bundle_images, (
                    f"Should update {num_bundle_images} images"
                )

            # NOTE: Media deduplication may reduce findScenes calls
            if num_bundle_scenes > 0:
                assert len(find_scenes_calls) >= 1, (
                    "Should call findScenes at least once (deduplication may reduce calls)"
                )
                assert len(scene_update_calls) >= num_bundle_scenes, (
                    f"Should update {num_bundle_scenes} scenes"
                )

            # Each media should trigger performer lookups (name + alias)
            total_media = num_bundle_images + num_bundle_scenes
            assert len(find_performers_calls) >= total_media * 2, (
                f"Should call findPerformers at least {total_media * 2} times "
                f"({total_media} media x 2 lookups each)"
            )

            # Should search for studios
            assert len(find_studios_calls) >= 2, (
                "Should search for studios at least twice"
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
        factory_session,
        real_stash_processor,
        test_database_sync,
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
            account = AccountFactory(username="attachment_user")
            factory_session.commit()

            # Create a real post with date matching the file
            post = PostFactory(
                accountId=account.id,
                content="Attachment post",
                createdAt=file_date,
            )
            factory_session.commit()

            # Create real media with extracted ID
            media = MediaFactory(
                id=media_id,
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,
            )
            factory_session.commit()

            # Create real AccountMedia to link media to account
            account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
            factory_session.commit()

            # Create real attachment with proper ContentType
            attachment = AttachmentFactory(
                contentId=account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                postId=post.id,
            )
            factory_session.commit()

            # Process in async session with proper relationship loading
            async with test_database_sync.async_session_scope() as async_session:
                from metadata.post import Post

                # Eager-load all relationships
                result_query = await async_session.execute(
                    select(Attachment)
                    .options(
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.bundle),
                        selectinload(Attachment.aggregated_post),
                    )
                    .where(Attachment.id == attachment.id)
                )
                async_attachment = result_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                # Capture GraphQL calls for validation
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=async_attachment,
                        item=async_post,
                        account=async_account,
                        session=async_session,
                    )

            # Verify expected GraphQL call sequence with exact call-by-call validation
            # Expected: findImages → findPerformers(name) → findPerformers(alias) →
            #           findStudios(Fansly) → findStudios(creator) → [studioCreate?] → imageUpdate
            assert len(calls) >= 6, (
                f"Expected at least 6 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findImages
            assert "findImages" in calls[0]["query"]
            assert "image_filter" in calls[0]["variables"]
            assert "findImages" in calls[0]["result"]

            # Call 1: findPerformers (by name)
            assert "findPerformers" in calls[1]["query"]
            assert (
                calls[1]["variables"]["performer_filter"]["name"]["value"]
                == account.username
            )
            assert "findPerformers" in calls[1]["result"]

            # Call 2: findPerformers (by alias)
            assert "findPerformers" in calls[2]["query"]
            assert "findPerformers" in calls[2]["result"]
            assert (
                calls[2]["variables"]["performer_filter"]["aliases"]["value"]
                == account.username
            )

            # Call 3: findStudios for Fansly (network)
            assert "findStudios" in calls[3]["query"]
            assert calls[3]["variables"]["filter"]["q"] == "Fansly (network)"
            assert "findStudios" in calls[3]["result"]

            # Call 4: findStudios for creator studio
            assert "findStudios" in calls[4]["query"]
            assert (
                calls[4]["variables"]["filter"]["q"] == f"{account.username} (Fansly)"
            )
            assert "findStudios" in calls[4]["result"]

            # Call 5: Conditional - either studioCreate OR imageUpdate
            if "studioCreate" in calls[5]["query"]:
                # Studio was created
                assert (
                    calls[5]["variables"]["input"]["name"]
                    == f"{account.username} (Fansly)"
                )
                assert "studioCreate" in calls[5]["result"]
                created_studio_id = calls[5]["result"]["studioCreate"]["id"]
                cleanup["studios"].append(created_studio_id)

                # Call 6: imageUpdate (when studio was created)
                assert len(calls) == 7, (
                    f"Expected 7 calls when studio created, got {len(calls)}"
                )
                assert "imageUpdate" in calls[6]["query"] or "execute" in str(calls[6])
                # Verify image update includes studio_id
                if "imageUpdate" in calls[6]["query"]:
                    assert "input" in calls[6]["variables"]
                    assert "studio_id" in calls[6]["variables"]["input"]
                assert (
                    "imageUpdate" in calls[6]["result"] or "data" in calls[6]["result"]
                )
            else:
                # Studio already existed, went straight to imageUpdate
                assert len(calls) == 6, (
                    f"Expected 6 calls when studio exists, got {len(calls)}"
                )
                assert "imageUpdate" in calls[5]["query"] or "execute" in str(calls[5])
                # Verify image update includes studio_id
                if "imageUpdate" in calls[5]["query"]:
                    assert "input" in calls[5]["variables"]
                    assert "studio_id" in calls[5]["variables"]["input"]
                assert (
                    "imageUpdate" in calls[5]["result"] or "data" in calls[5]["result"]
                )

            # Verify results structure
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
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
            account = AccountFactory(username="bundle_attachment_test_user")
            factory_session.commit()

            # Create a real post with date matching the file
            post = PostFactory(
                accountId=account.id,
                content="Bundle attachment post",
                createdAt=file_date,
            )
            factory_session.commit()

            # Create real media with extracted ID
            media = MediaFactory(
                id=media_id,
                accountId=account.id,
                mimetype="image/jpeg",
                type=1,
                is_downloaded=True,
                stash_id=None,
                local_filename=image_file_path,
            )
            factory_session.commit()

            # Create real AccountMedia to link media to account
            account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
            factory_session.commit()

            # Create real bundle with media
            bundle = AccountMediaBundleFactory(accountId=account.id)
            factory_session.commit()

            # Link media to bundle
            factory_session.execute(
                account_media_bundle_media.insert().values(
                    [
                        {
                            "bundle_id": bundle.id,
                            "media_id": account_media.id,
                            "pos": 0,
                        },
                    ]
                )
            )
            factory_session.commit()

            # Create attachment pointing to bundle
            attachment = AttachmentFactory(
                contentId=bundle.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                postId=post.id,
            )
            factory_session.commit()

            # Process in async session with proper relationship loading
            async with test_database_sync.async_session_scope() as async_session:
                # Eager-load all relationships
                result_query = await async_session.execute(
                    select(Attachment)
                    .options(
                        selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.aggregated_post),
                    )
                    .where(Attachment.id == attachment.id)
                )
                async_attachment = result_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                # Capture GraphQL calls for validation
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=async_attachment,
                        item=async_post,
                        account=async_account,
                        session=async_session,
                    )

            # Verify expected GraphQL call sequence with exact call-by-call validation
            # Expected: findImages → findPerformers(name) → findPerformers(alias) →
            #           findStudios(Fansly) → findStudios(creator) → [studioCreate?] → imageUpdate
            assert len(calls) >= 6, (
                f"Expected at least 6 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findImages
            assert "findImages" in calls[0]["query"]
            assert "image_filter" in calls[0]["variables"]
            assert "findImages" in calls[0]["result"]

            # Call 1: findPerformers (by name)
            assert "findPerformers" in calls[1]["query"]
            assert (
                calls[1]["variables"]["performer_filter"]["name"]["value"]
                == account.username
            )
            assert "findPerformers" in calls[1]["result"]

            # Call 2: findPerformers (by alias)
            assert "findPerformers" in calls[2]["query"]
            assert "findPerformers" in calls[2]["result"]
            assert (
                calls[2]["variables"]["performer_filter"]["aliases"]["value"]
                == account.username
            )

            # Call 3: findStudios for Fansly (network)
            assert "findStudios" in calls[3]["query"]
            assert calls[3]["variables"]["filter"]["q"] == "Fansly (network)"
            assert "findStudios" in calls[3]["result"]

            # Call 4: findStudios for creator studio
            assert "findStudios" in calls[4]["query"]
            assert (
                calls[4]["variables"]["filter"]["q"] == f"{account.username} (Fansly)"
            )
            assert "findStudios" in calls[4]["result"]

            # Call 5: Conditional - either studioCreate OR imageUpdate
            if "studioCreate" in calls[5]["query"]:
                # Studio was created
                assert (
                    calls[5]["variables"]["input"]["name"]
                    == f"{account.username} (Fansly)"
                )
                assert "studioCreate" in calls[5]["result"]
                created_studio_id = calls[5]["result"]["studioCreate"]["id"]
                cleanup["studios"].append(created_studio_id)

                # Call 6: imageUpdate (when studio was created)
                assert len(calls) == 7, (
                    f"Expected 7 calls when studio created, got {len(calls)}"
                )
                assert "imageUpdate" in calls[6]["query"] or "execute" in str(calls[6])
                # Verify image update includes studio_id
                if "imageUpdate" in calls[6]["query"]:
                    assert "input" in calls[6]["variables"]
                    assert "studio_id" in calls[6]["variables"]["input"]
                assert (
                    "imageUpdate" in calls[6]["result"] or "data" in calls[6]["result"]
                )
            else:
                # Studio already existed, went straight to imageUpdate
                assert len(calls) == 6, (
                    f"Expected 6 calls when studio exists, got {len(calls)}"
                )
                assert "imageUpdate" in calls[5]["query"] or "execute" in str(calls[5])
                # Verify image update includes studio_id
                if "imageUpdate" in calls[5]["query"]:
                    assert "input" in calls[5]["variables"]
                    assert "studio_id" in calls[5]["variables"]["input"]
                assert (
                    "imageUpdate" in calls[5]["result"] or "data" in calls[5]["result"]
                )

            # Verify bundle was processed and results collected
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
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

            # Process in async session - create all objects in async context
            async with test_database_sync.async_session_scope() as async_session:
                # Create account in async session
                account = AccountFactory.build(username="aggregated_post_test_user")
                async_session.add(account)
                await async_session.flush()

                # Create parent post
                parent_post = PostFactory.build(
                    accountId=account.id, content="Parent post", createdAt=file_date
                )
                async_session.add(parent_post)
                await async_session.flush()

                # Create aggregated post
                agg_post = PostFactory.build(
                    accountId=account.id,
                    content="Aggregated post",
                    createdAt=file_date,
                )
                async_session.add(agg_post)
                await async_session.flush()

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
                async_session.add(media)
                await async_session.flush()

                # Create AccountMedia
                account_media = AccountMediaFactory.build(
                    accountId=account.id, mediaId=media.id
                )
                async_session.add(account_media)
                await async_session.flush()

                # Create attachment for aggregated post pointing to the media
                agg_attachment = AttachmentFactory.build(
                    contentId=account_media.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    postId=agg_post.id,
                )
                async_session.add(agg_attachment)
                await async_session.flush()

                # Create attachment for parent post with AGGREGATED_POSTS type
                parent_attachment = AttachmentFactory.build(
                    contentId=agg_post.id,
                    contentType=ContentType.AGGREGATED_POSTS,
                    postId=parent_post.id,
                )
                parent_attachment.aggregated_post = agg_post
                async_session.add(parent_attachment)
                await async_session.flush()

                # Load relationships using awaitable_attrs to avoid lazy loads in async context
                if hasattr(parent_attachment, "awaitable_attrs"):
                    await parent_attachment.awaitable_attrs.aggregated_post
                    await parent_attachment.awaitable_attrs.media
                    await parent_attachment.awaitable_attrs.bundle
                if hasattr(agg_post, "awaitable_attrs"):
                    await agg_post.awaitable_attrs.attachments

                # Also need to load relationships on the aggregated post's attachments
                # since they will be processed recursively
                for agg_att in agg_post.attachments:
                    if hasattr(agg_att, "awaitable_attrs"):
                        await agg_att.awaitable_attrs.media
                        await agg_att.awaitable_attrs.bundle
                        await agg_att.awaitable_attrs.aggregated_post

                # Capture GraphQL calls for validation
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    result = await real_stash_processor.process_creator_attachment(
                        attachment=parent_attachment,
                        item=parent_post,
                        account=account,
                        session=async_session,
                    )

            # Verify expected GraphQL calls - should process the aggregated post's media
            # Expected: findImages → findPerformers(name) → findPerformers(alias) →
            #           findStudios(Fansly) → findStudios(creator) → [studioCreate?] → imageUpdate
            assert len(calls) >= 6, (
                f"Expected at least 6 GraphQL calls for aggregated post media, got {len(calls)}"
            )

            # Call 0: findImages
            assert "findImages" in calls[0]["query"]
            assert "image_filter" in calls[0]["variables"]
            assert "findImages" in calls[0]["result"]

            # Call 1: findPerformers (by name)
            assert "findPerformers" in calls[1]["query"]
            assert (
                calls[1]["variables"]["performer_filter"]["name"]["value"]
                == account.username
            )
            assert "findPerformers" in calls[1]["result"]

            # Call 2: findPerformers (by alias)
            assert "findPerformers" in calls[2]["query"]
            assert "findPerformers" in calls[2]["result"]

            # Call 3: findStudios for Fansly (network)
            assert "findStudios" in calls[3]["query"]
            assert calls[3]["variables"]["filter"]["q"] == "Fansly (network)"

            # Call 4: findStudios for creator studio
            assert "findStudios" in calls[4]["query"]
            assert (
                calls[4]["variables"]["filter"]["q"] == f"{account.username} (Fansly)"
            )

            # Call 5: Conditional - either studioCreate OR imageUpdate
            if "studioCreate" in calls[5]["query"]:
                # Studio was created
                created_studio_id = calls[5]["result"]["studioCreate"]["id"]
                cleanup["studios"].append(created_studio_id)
                assert len(calls) == 7
                assert "imageUpdate" in calls[6]["query"] or "execute" in str(calls[6])
            else:
                # Studio already existed
                assert len(calls) == 6
                assert "imageUpdate" in calls[5]["query"] or "execute" in str(calls[5])

            # Verify results include aggregated content
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result
            assert len(result["images"]) >= 1
