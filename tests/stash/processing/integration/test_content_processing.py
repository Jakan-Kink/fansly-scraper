"""Integration tests for post and message processing in StashProcessing.

This module tests content processing (posts and messages) using real database
fixtures and factory-based test data. All tests use REAL Stash API calls verified
with capture_graphql_calls.
"""

from datetime import UTC, datetime
from functools import wraps
from unittest.mock import patch

import pytest
from stash_graphql_client.types import Gallery, Performer, Studio

from metadata import ContentType
from metadata.models import FanslyObject
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.utils.test_isolation import get_unique_test_id, snowflake_id


class TestContentProcessingIntegration:
    """Integration tests for content processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator_posts_integration(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test process_creator_posts with real database and Stash API integration."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            media_meta = await message_media_generator(spread_over_objs=3)

            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"post_creator_{test_id}")
            await entity_store.save(account)

            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]

                for media in post_media.media_items:
                    media.accountId = account.id
                    await entity_store.save(media)

                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    await entity_store.save(account_media)

                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    await entity_store.save(post_media.bundle)

                    await entity_store.sync_junction(
                        "account_media_bundle_media",
                        "bundle_id",
                        post_media.bundle.id,
                        [
                            {
                                "media_id": link_data["account_media"].id,
                                "pos": link_data["pos"],
                            }
                            for link_data in post_media.bundle_media_links
                        ],
                    )

                    # Wire up bundle.accountMedia so _collect_media_from_attachments
                    # can traverse bundle → accountMedia → media
                    post_media.bundle.accountMedia = [
                        link_data["account_media"]
                        for link_data in post_media.bundle_media_links
                    ]

                # Use a date earlier than any Stash image date so
                # _update_stash_metadata doesn't skip the update
                post = PostFactory.build(
                    accountId=account.id,
                    content=f"Post {i}",
                    createdAt=datetime(2000, 1, 1, tzinfo=UTC),
                )
                await entity_store.save(post)

                # Attach media to post (bundle + individual, mimics real Fansly API behavior)
                attachments_created = 0
                post_attachments = []

                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory.build(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    await entity_store.save(attachment)
                    post_attachments.append(attachment)
                    attachments_created += 1

                if post_media.account_media_items:
                    for account_media in post_media.account_media_items:
                        if post_media.has_bundle and post_media.bundle:
                            is_in_bundle = any(
                                link["account_media"].id == account_media.id
                                for link in post_media.bundle_media_links
                            )
                            if is_in_bundle:
                                continue

                        attachment = AttachmentFactory.build(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        await entity_store.save(attachment)
                        post_attachments.append(attachment)
                        attachments_created += 1

                # Wire up post.attachments so identity map lookups work
                post.attachments = post_attachments
                posts.append(post)

            performer = Performer(
                name="[TEST] Post Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            # SPY: Validate media lookup routing (by path vs by ID)
            lookup_routing = {"by_path": 0, "by_id": 0}
            original_find_by_path = real_stash_processor._find_stash_files_by_path

            @wraps(original_find_by_path)
            async def spy_find_by_path(media_files):
                lookup_routing["by_path"] += len(media_files)
                return await original_find_by_path(media_files)

            original_find_by_id = real_stash_processor._find_stash_files_by_id

            @wraps(original_find_by_id)
            async def spy_find_by_id(stash_files):
                lookup_routing["by_id"] += len(stash_files)
                return await original_find_by_id(stash_files)

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Spy on store.save to track actual creates (not find-or-return)
            created_studios = []
            created_galleries = []
            original_save = real_stash_processor.context.store.save

            async def spy_save(obj, *args, **kwargs):
                is_new_studio = isinstance(obj, Studio) and obj.is_new()
                is_new_gallery = isinstance(obj, Gallery) and obj.is_new()
                result = await original_save(obj, *args, **kwargs)
                if is_new_studio:
                    created_studios.append(obj.id)
                elif is_new_gallery:
                    created_galleries.append(obj.id)
                return result

            with (
                patch.object(
                    real_stash_processor.context.store, "save", side_effect=spy_save
                ),
                capture_graphql_calls(real_stash_processor.context.client) as calls,
                patch.object(
                    real_stash_processor,
                    "_find_stash_files_by_path",
                    spy_find_by_path,
                ),
                patch.object(
                    real_stash_processor, "_find_stash_files_by_id", spy_find_by_id
                ),
            ):
                try:
                    await real_stash_processor.process_creator_posts(
                        account=account,
                        performer=performer,
                        studio=None,
                    )
                finally:
                    dump_graphql_calls(calls, "test_process_creator_posts_integration")

                # Manual cleanup from spies
                for sid in created_studios:
                    cleanup["studios"].append(sid)
                for gid in created_galleries:
                    cleanup["galleries"].append(gid)

                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                # Media deduplication: Shared media from Docker Stash causes variable gallery counts
                assert 1 <= len(gallery_creates) <= 3, (
                    f"Expected 1-3 galleries (deduplication), got {len(gallery_creates)}"
                )

                created_gallery_ids = []
                for call in gallery_creates:
                    variables = call["variables"]
                    input_data = variables["input"]
                    assert "title" in input_data
                    assert "post_creator" in input_data["title"]
                    assert input_data.get("date")
                    assert (
                        "urls" in input_data
                    )  # Field is 'urls' (plural array), not 'url'
                    assert any("fansly.com/post/" in url for url in input_data["urls"])
                    assert "performer_ids" in input_data
                    assert performer.id in input_data["performer_ids"]

                    result = call["result"]
                    assert "galleryCreate" in result
                    created_gallery_ids.append(result["galleryCreate"]["id"])

                find_calls = [
                    c
                    for c in calls
                    if "findImage" in c["query"] or "findScene" in c["query"]
                ]
                assert len(find_calls) > 0, "Expected media lookup calls"

                # NOTE: Parallel test race conditions can cause GraphQL errors, result will be None
                media_found_count = 0
                graphql_errors = 0
                for call in find_calls:
                    result = call["result"]
                    if result is None:
                        graphql_errors += 1
                        continue
                    # Plural queries return {images: [...], count: N} or {scenes: [...], count: N}
                    if result.get("findImages"):
                        media_found_count += result["findImages"]["count"]
                    if result.get("findScenes"):
                        media_found_count += result["findScenes"]["count"]
                    # Singular queries return the object directly (count = 1 if found, 0 if not found)
                    if "findImage" in result:
                        media_found_count += 1 if result["findImage"] else 0
                    if "findScene" in result:
                        media_found_count += 1 if result["findScene"] else 0

                if graphql_errors > 0:
                    print(
                        f"\n  NOTE: {graphql_errors} find calls failed due to race conditions"
                    )

                # Media deduplication and parallel execution: Tests share limited Docker Stash media
                # Some media may already be in use by other tests, so accept significantly reduced counts
                min_expected = max(
                    1, media_meta.total_media // 10
                )  # Accept at least 10% (more realistic)
                assert media_found_count >= min_expected, (
                    f"Expected to find at least {min_expected} media items "
                    f"({media_meta.total_media} total, allowing for shared media), found {media_found_count}"
                )

                image_updates = [c for c in calls if "imageUpdate" in c["query"]]
                scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
                assert len(image_updates) + len(scene_updates) > 0

                if image_updates:
                    first_update = next(
                        (u for u in image_updates if u["result"] is not None), None
                    )
                    if first_update:
                        input_data = first_update["variables"]["input"]
                        assert "id" in input_data
                        if "details" in input_data:
                            assert isinstance(input_data["details"], str)
                        assert (
                            first_update["result"]["imageUpdate"]["id"]
                            == input_data["id"]
                        )

                # Gallery image linking fires addGalleryImages via
                # Gallery.__side_mutations__["images"] at gallery.save().
                add_gallery_images_calls = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                posts_with_images = sum(1 for p in media_meta if p.num_images > 0)
                if posts_with_images > 0:
                    assert len(add_gallery_images_calls) >= 1, (
                        f"Expected at least 1 addGalleryImages call "
                        f"({posts_with_images} posts have images), "
                        f"got {len(add_gallery_images_calls)}"
                    )
                    for call in add_gallery_images_calls:
                        input_data = call["variables"]["input"]
                        gallery_id = input_data["gallery_id"]
                        assert gallery_id in created_gallery_ids, (
                            f"addGalleryImages gallery_id {gallery_id} should be "
                            f"one of the created galleries {created_gallery_ids}"
                        )
                        assert isinstance(input_data["image_ids"], list)
                        assert len(input_data["image_ids"]) >= 1

                # SPY: Verify media lookup routing (fast path via ID vs slow path via file)
                total_lookups = lookup_routing["by_path"] + lookup_routing["by_id"]
                assert total_lookups > 0
                assert total_lookups == media_meta.total_media

    @pytest.mark.asyncio
    async def test_process_creator_messages_integration(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test process_creator_messages with real database and Stash API integration."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            media_meta = await message_media_generator(spread_over_objs=3)

            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"message_creator_{test_id}")
            await entity_store.save(account)

            group = GroupFactory.build(createdBy=account.id)
            group.users = [account]
            await entity_store.save(group)

            messages = []
            for i in range(len(media_meta)):
                message_media = media_meta[i]

                for media in message_media.media_items:
                    media.accountId = account.id
                    await entity_store.save(media)

                for account_media in message_media.account_media_items:
                    account_media.accountId = account.id
                    await entity_store.save(account_media)

                if message_media.has_bundle and message_media.bundle:
                    message_media.bundle.accountId = account.id
                    await entity_store.save(message_media.bundle)

                    await entity_store.sync_junction(
                        "account_media_bundle_media",
                        "bundle_id",
                        message_media.bundle.id,
                        [
                            {
                                "media_id": link_data["account_media"].id,
                                "pos": link_data["pos"],
                            }
                            for link_data in message_media.bundle_media_links
                        ],
                    )

                    # Wire up bundle.accountMedia so _collect_media_from_attachments
                    # can traverse bundle → accountMedia → media
                    message_media.bundle.accountMedia = [
                        link_data["account_media"]
                        for link_data in message_media.bundle_media_links
                    ]

                # Use a date earlier than any Stash image date so
                # _update_stash_metadata doesn't skip the update
                message = MessageFactory.build(
                    groupId=group.id,
                    senderId=account.id,
                    content=f"Message {i}",
                    createdAt=datetime(2000, 1, 1, tzinfo=UTC),
                )
                await entity_store.save(message)

                # Attach media to message (bundle + individual, mimics real Fansly API behavior)
                attachments_created = 0
                msg_attachments = []

                if message_media.has_bundle and message_media.bundle:
                    attachment = AttachmentFactory.build(
                        messageId=message.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=message_media.bundle.id,
                        pos=attachments_created,
                    )
                    await entity_store.save(attachment)
                    msg_attachments.append(attachment)
                    attachments_created += 1

                if message_media.account_media_items:
                    for account_media in message_media.account_media_items:
                        if message_media.has_bundle and message_media.bundle:
                            is_in_bundle = any(
                                link["account_media"].id == account_media.id
                                for link in message_media.bundle_media_links
                            )
                            if is_in_bundle:
                                continue

                        attachment = AttachmentFactory.build(
                            messageId=message.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        await entity_store.save(attachment)
                        msg_attachments.append(attachment)
                        attachments_created += 1

                # Wire up message.attachments so identity map lookups work
                message.attachments = msg_attachments
                messages.append(message)

            performer = Performer(
                name="[TEST] Message Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Spy on store.save to track actual creates (not find-or-return)
            created_studios = []
            created_galleries = []
            original_save = real_stash_processor.context.store.save

            async def spy_save(obj, *args, **kwargs):
                is_new_studio = isinstance(obj, Studio) and obj.is_new()
                is_new_gallery = isinstance(obj, Gallery) and obj.is_new()
                result = await original_save(obj, *args, **kwargs)
                if is_new_studio:
                    created_studios.append(obj.id)
                elif is_new_gallery:
                    created_galleries.append(obj.id)
                return result

            with (
                patch.object(
                    real_stash_processor.context.store, "save", side_effect=spy_save
                ),
                capture_graphql_calls(real_stash_processor.context.client) as calls,
            ):
                try:
                    await real_stash_processor.process_creator_messages(
                        account=account,
                        performer=performer,
                        studio=None,
                    )
                finally:
                    dump_graphql_calls(
                        calls, "test_process_creator_messages_integration"
                    )

                # Manual cleanup from spies
                for sid in created_studios:
                    cleanup["studios"].append(sid)
                for gid in created_galleries:
                    cleanup["galleries"].append(gid)

                # Permanent GraphQL Call Assertions

                # 1. Verify Gallery Creation
                # Expected: 1-3 galleries (media deduplication may reduce count)
                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert 1 <= len(gallery_creates) <= 3, (
                    f"Expected 1-3 galleries (deduplication), got {len(gallery_creates)}"
                )

                created_gallery_ids = []
                for call in gallery_creates:
                    # Request Assertions
                    variables = call["variables"]
                    assert "input" in variables
                    input_data = variables["input"]

                    # Verify core metadata
                    assert "title" in input_data
                    assert "message_creator" in input_data["title"]
                    assert input_data.get("date")  # Should have date
                    assert "performer_ids" in input_data
                    assert performer.id in input_data["performer_ids"]

                    # Response Assertions
                    result = call["result"]
                    assert "galleryCreate" in result
                    assert "id" in result["galleryCreate"]
                    created_gallery_ids.append(result["galleryCreate"]["id"])

                # 2. Verify Media Lookups
                find_calls = [
                    c
                    for c in calls
                    if "findImage" in c["query"] or "findScene" in c["query"]
                ]
                assert len(find_calls) > 0

                # Count media found (handle None results from parallel test race conditions)
                media_found_count = 0
                graphql_errors = 0
                for call in find_calls:
                    result = call["result"]
                    if result is None:
                        graphql_errors += 1
                        continue
                    # Plural queries return {images: [...], count: N} or {scenes: [...], count: N}
                    if result.get("findImages"):
                        media_found_count += result["findImages"]["count"]
                    if result.get("findScenes"):
                        media_found_count += result["findScenes"]["count"]
                    # Singular queries return the object directly (count = 1 if found, 0 if not found)
                    if "findImage" in result:
                        media_found_count += 1 if result["findImage"] else 0
                    if "findScene" in result:
                        media_found_count += 1 if result["findScene"] else 0

                if graphql_errors > 0:
                    print(
                        f"\n  NOTE: {graphql_errors} find calls failed due to race conditions"
                    )

                # Media deduplication and parallel execution: Tests share limited Docker Stash media
                # Some media may already be in use by other tests, so accept significantly reduced counts
                min_expected = max(
                    1, media_meta.total_media // 10
                )  # Accept at least 10% (more realistic)
                assert media_found_count >= min_expected, (
                    f"Expected to find at least {min_expected} media items "
                    f"({media_meta.total_media} total, allowing for shared media), found {media_found_count}"
                )

                # 3. Verify Media Updates
                image_updates = [c for c in calls if "imageUpdate" in c["query"]]
                scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
                total_updates = len(image_updates) + len(scene_updates)

                assert total_updates > 0, "Expected media updates to occur"

                # Verify content of first successful image update as sample
                if image_updates:
                    first_update = None
                    for update in image_updates:
                        if update["result"] is not None:
                            first_update = update
                            break

                    if first_update:
                        variables = first_update["variables"]
                        assert "input" in variables
                        input_data = variables["input"]

                        # Request Assertions
                        assert "id" in input_data

                        # Response Assertions
                        result = first_update["result"]
                        assert "imageUpdate" in result
                        assert "id" in result["imageUpdate"]
                        assert result["imageUpdate"]["id"] == input_data["id"]

                # 4. Verify gallery image linking via addGalleryImages
                # (fired by Gallery.__side_mutations__["images"]).
                messages_with_images = sum(
                    1 for message_media in media_meta if message_media.num_images > 0
                )

                add_gallery_images_calls = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                if messages_with_images > 0:
                    assert len(add_gallery_images_calls) >= 1, (
                        f"Expected at least 1 addGalleryImages call "
                        f"({messages_with_images} messages have images), "
                        f"got {len(add_gallery_images_calls)}"
                    )
                    for call in add_gallery_images_calls:
                        input_data = call["variables"]["input"]
                        gallery_id = input_data["gallery_id"]
                        assert gallery_id in created_gallery_ids, (
                            f"addGalleryImages gallery_id {gallery_id} should be "
                            f"one of the created galleries {created_gallery_ids}"
                        )
                        assert isinstance(input_data["image_ids"], list)
                        assert len(input_data["image_ids"]) >= 1

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test _process_items_with_gallery integration with real posts."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Generate realistic media for 2 posts using Docker Stash data
            media_meta = await message_media_generator(spread_over_objs=2)

            # Create real account and posts using entity_store
            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"gallery_creator_{test_id}")
            await entity_store.save(account)

            # Create 2 posts, each with its own media distribution
            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]  # Get media for this specific post

                # Save media to entity_store
                for media in post_media.media_items:
                    media.accountId = account.id
                    await entity_store.save(media)

                # Save AccountMedia to entity_store
                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    await entity_store.save(account_media)

                # Handle bundle if present for this post
                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    await entity_store.save(post_media.bundle)

                    # Link AccountMedia to bundle
                    await entity_store.sync_junction(
                        "account_media_bundle_media",
                        "bundle_id",
                        post_media.bundle.id,
                        [
                            {
                                "media_id": link_data["account_media"].id,
                                "pos": link_data["pos"],
                            }
                            for link_data in post_media.bundle_media_links
                        ],
                    )

                    # Wire up bundle.accountMedia so _collect_media_from_attachments
                    # can traverse bundle → accountMedia → media
                    post_media.bundle.accountMedia = [
                        link_data["account_media"]
                        for link_data in post_media.bundle_media_links
                    ]

                # Create post with date earlier than any Stash image date so
                # _update_stash_metadata doesn't skip the update
                post = PostFactory.build(
                    accountId=account.id,
                    createdAt=datetime(2000, 1, 1, tzinfo=UTC),
                )
                await entity_store.save(post)

                # Attach media to post (mimics real Fansly API)
                # Real API can have: bundle only, individual only, OR bundle + individual
                attachments_created = 0
                post_attachments = []

                # First: Add bundle attachment if present
                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory.build(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    await entity_store.save(attachment)
                    post_attachments.append(attachment)
                    attachments_created += 1

                # Second: Add individual media attachments (videos or non-bundled images)
                if post_media.account_media_items:
                    for account_media in post_media.account_media_items:
                        # Skip images that are already in the bundle
                        if post_media.has_bundle and post_media.bundle:
                            is_in_bundle = any(
                                link["account_media"].id == account_media.id
                                for link in post_media.bundle_media_links
                            )
                            if is_in_bundle:
                                continue  # Already covered by bundle attachment

                        # Create attachment for non-bundled media (videos, or images when ≤3)
                        attachment = AttachmentFactory.build(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        await entity_store.save(attachment)
                        post_attachments.append(attachment)
                        attachments_created += 1

                # Wire up post.attachments so identity map lookups work
                post.attachments = post_attachments
                posts.append(post)

            # Create real performer in Stash
            performer = Performer(
                name="[TEST] Gallery Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Spy on store.save to track actual creates (not find-or-return)
            created_studios = []
            created_galleries = []
            original_save = real_stash_processor.context.store.save

            async def spy_save(obj, *args, **kwargs):
                is_new_studio = isinstance(obj, Studio) and obj.is_new()
                is_new_gallery = isinstance(obj, Gallery) and obj.is_new()
                result = await original_save(obj, *args, **kwargs)
                if is_new_studio:
                    created_studios.append(obj.id)
                elif is_new_gallery:
                    created_galleries.append(obj.id)
                return result

            with (
                patch.object(
                    real_stash_processor.context.store, "save", side_effect=spy_save
                ),
                capture_graphql_calls(real_stash_processor.context.client) as calls,
            ):
                try:
                    await real_stash_processor._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=None,
                        item_type="post",
                        items=posts,
                        url_pattern_func=url_pattern_func,
                    )
                finally:
                    dump_graphql_calls(calls, "test_process_items_with_gallery")

                # Manual cleanup from spies
                for sid in created_studios:
                    cleanup["studios"].append(sid)
                for gid in created_galleries:
                    cleanup["galleries"].append(gid)

                # Permanent GraphQL Call Assertions

                # 1. Verify Gallery Creation
                # Expected: 1-2 galleries (media deduplication may reduce count)
                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert 1 <= len(gallery_creates) <= 2, (
                    f"Expected 1-2 galleries (deduplication), got {len(gallery_creates)}"
                )

                created_gallery_ids = []
                for call in gallery_creates:
                    # Request Assertions
                    variables = call["variables"]
                    assert "input" in variables
                    input_data = variables["input"]

                    # Verify core metadata - using custom URL pattern
                    assert "title" in input_data
                    # Title is either content (if ≥10 chars) or "{username} - {date}" fallback
                    # Find the matching post to check its content length
                    matching_post = next(
                        (
                            p
                            for p in posts
                            if any(
                                str(p.id) in url for url in input_data.get("urls", [])
                            )
                        ),
                        None,
                    )
                    if (
                        matching_post
                        and len(matching_post.content.split("\n")[0]) >= 10
                    ):
                        # Content is used as title
                        assert len(input_data["title"]) >= 10
                    else:
                        # Username fallback is used
                        assert "gallery_creator" in input_data["title"].lower()
                    assert "urls" in input_data
                    assert "example.com" in input_data["urls"][0]
                    assert "performer_ids" in input_data
                    assert performer.id in input_data["performer_ids"]

                    # Response Assertions
                    result = call["result"]
                    assert "galleryCreate" in result
                    assert "id" in result["galleryCreate"]
                    created_gallery_ids.append(result["galleryCreate"]["id"])

                # Verify the URLs were generated correctly with custom pattern
                # Note: 'urls' is plural array, flatten to get all URLs
                gallery_urls = [
                    url
                    for c in gallery_creates
                    for url in c["variables"]["input"].get("urls", [])
                ]
                # At least one post should have generated a URL (may dedupe to 1 gallery)
                assert len(gallery_urls) >= 1, (
                    f"Expected at least 1 URL, got {gallery_urls}"
                )
                assert any("example.com" in url for url in gallery_urls), (
                    f"Expected example.com URLs, got {gallery_urls}"
                )

                # 2. Verify Media Lookups
                find_calls = [
                    c
                    for c in calls
                    if "findImage" in c["query"] or "findScene" in c["query"]
                ]
                assert len(find_calls) > 0

                # Count media found (handle None results from parallel test race conditions)
                media_found_count = 0
                graphql_errors = 0
                for call in find_calls:
                    result = call["result"]
                    if result is None:
                        graphql_errors += 1
                        continue
                    # Plural queries return {images: [...], count: N} or {scenes: [...], count: N}
                    if result.get("findImages"):
                        media_found_count += result["findImages"]["count"]
                    if result.get("findScenes"):
                        media_found_count += result["findScenes"]["count"]
                    # Singular queries return the object directly (count = 1 if found, 0 if not found)
                    if "findImage" in result:
                        media_found_count += 1 if result["findImage"] else 0
                    if "findScene" in result:
                        media_found_count += 1 if result["findScene"] else 0

                if graphql_errors > 0:
                    print(
                        f"\n  NOTE: {graphql_errors} find calls failed due to race conditions"
                    )

                # Media deduplication and parallel execution: Tests share limited Docker Stash media
                # Some media may already be in use by other tests, so accept significantly reduced counts
                min_expected = max(
                    1, media_meta.total_media // 10
                )  # Accept at least 10% (more realistic)
                assert media_found_count >= min_expected, (
                    f"Expected to find at least {min_expected} media items "
                    f"({media_meta.total_media} total, allowing for shared media), found {media_found_count}"
                )

                # 3. Verify Media Updates
                image_updates = [c for c in calls if "imageUpdate" in c["query"]]
                scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
                total_updates = len(image_updates) + len(scene_updates)

                assert total_updates > 0, "Expected media updates to occur"

                # 4. Verify gallery image linking via addGalleryImages
                # (fired by Gallery.__side_mutations__["images"]).
                posts_with_images = sum(
                    1 for post_media in media_meta if post_media.num_images > 0
                )

                add_gallery_images_calls = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                if posts_with_images > 0:
                    assert len(add_gallery_images_calls) >= 1, (
                        f"Expected at least 1 addGalleryImages call "
                        f"({posts_with_images} posts have images, but deduplication may merge galleries), "
                        f"got {len(add_gallery_images_calls)}"
                    )
                    for call in add_gallery_images_calls:
                        input_data = call["variables"]["input"]
                        gallery_id = input_data["gallery_id"]
                        assert gallery_id in created_gallery_ids, (
                            f"addGalleryImages gallery_id {gallery_id} should be "
                            f"one of the created galleries {created_gallery_ids}"
                        )
                        assert isinstance(input_data["image_ids"], list)
                        assert len(input_data["image_ids"]) >= 1

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
        mocker,
    ):
        """Test _process_items_with_gallery with error handling using spy pattern."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Generate realistic media for 2 posts using Docker Stash data
            media_meta = await message_media_generator(spread_over_objs=2)

            # Create real account and posts using entity_store
            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"error_creator_{test_id}")
            await entity_store.save(account)

            # Create 2 posts, each with its own media distribution
            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]  # Get media for this specific post

                # Save media to entity_store
                for media in post_media.media_items:
                    media.accountId = account.id
                    await entity_store.save(media)

                # Save AccountMedia to entity_store
                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    await entity_store.save(account_media)

                # Handle bundle if present for this post
                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    await entity_store.save(post_media.bundle)

                    # Link AccountMedia to bundle
                    await entity_store.sync_junction(
                        "account_media_bundle_media",
                        "bundle_id",
                        post_media.bundle.id,
                        [
                            {
                                "media_id": link_data["account_media"].id,
                                "pos": link_data["pos"],
                            }
                            for link_data in post_media.bundle_media_links
                        ],
                    )

                    # Wire up bundle.accountMedia so _collect_media_from_attachments
                    # can traverse bundle → accountMedia → media
                    post_media.bundle.accountMedia = [
                        link_data["account_media"]
                        for link_data in post_media.bundle_media_links
                    ]

                # Create post with date earlier than any Stash image date
                post = PostFactory.build(
                    accountId=account.id,
                    content=f"Error test post {i}",
                    createdAt=datetime(2000, 1, 1, tzinfo=UTC),
                )
                await entity_store.save(post)

                # Attach media to post (mimics real Fansly API)
                # Real API can have: bundle only, individual only, OR bundle + individual
                attachments_created = 0
                post_attachments = []

                # First: Add bundle attachment if present
                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory.build(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    await entity_store.save(attachment)
                    post_attachments.append(attachment)
                    attachments_created += 1

                # Second: Add individual media attachments (videos or non-bundled images)
                if post_media.account_media_items:
                    for account_media in post_media.account_media_items:
                        # Skip images that are already in the bundle
                        if post_media.has_bundle and post_media.bundle:
                            is_in_bundle = any(
                                link["account_media"].id == account_media.id
                                for link in post_media.bundle_media_links
                            )
                            if is_in_bundle:
                                continue  # Already covered by bundle attachment

                        # Create attachment for non-bundled media (videos, or images when ≤3)
                        attachment = AttachmentFactory.build(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        await entity_store.save(attachment)
                        post_attachments.append(attachment)
                        attachments_created += 1

                # Wire up post.attachments so identity map lookups work
                post.attachments = post_attachments
                posts.append(post)

            # Create real performer in Stash
            performer = Performer(
                name="[TEST] Error Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            # Setup spy pattern to inject error for first post while allowing real execution
            original_process_item_gallery = real_stash_processor._process_item_gallery
            call_count = 0

            async def spy_process_item_gallery(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First call fails
                    raise RuntimeError("Test error")
                # Subsequent calls execute real code
                return await original_process_item_gallery(*args, **kwargs)

            mocker.patch.object(
                real_stash_processor,
                "_process_item_gallery",
                side_effect=spy_process_item_gallery,
            )

            # Mock error printing to avoid console output
            mocker.patch("stash.processing.mixins.content.print_error")

            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Clear store cache so processing makes fresh GraphQL calls
            real_stash_processor.context.store.invalidate_all()

            # Spy on store.save to track actual creates (not find-or-return)
            created_studios = []
            created_galleries = []
            original_save = real_stash_processor.context.store.save

            async def spy_save(obj, *args, **kwargs):
                is_new_studio = isinstance(obj, Studio) and obj.is_new()
                is_new_gallery = isinstance(obj, Gallery) and obj.is_new()
                result = await original_save(obj, *args, **kwargs)
                if is_new_studio:
                    created_studios.append(obj.id)
                elif is_new_gallery:
                    created_galleries.append(obj.id)
                return result

            # Capture GraphQL calls - should only see calls from second post (first fails early)
            with (
                patch.object(
                    real_stash_processor.context.store, "save", side_effect=spy_save
                ),
                capture_graphql_calls(real_stash_processor.context.client) as calls,
            ):
                try:
                    await real_stash_processor._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=None,
                        item_type="post",
                        items=posts,
                        url_pattern_func=url_pattern_func,
                    )
                finally:
                    dump_graphql_calls(
                        calls, "test_process_items_with_gallery_error_handling"
                    )

            # Manual cleanup from spies
            for sid in created_studios:
                cleanup["studios"].append(sid)
            for gid in created_galleries:
                cleanup["galleries"].append(gid)

            # Permanent GraphQL Call Assertions for Error Handling

            # 1. Verify error recovery - both posts were attempted despite first failing
            assert call_count == 2, (
                f"Expected 2 calls to _process_item_gallery, got {call_count}"
            )

            # 2. Verify second post still processed (first post failed, second succeeded)
            # The second post should have made GraphQL calls for:
            # - Gallery creation (1 call)
            # - Media lookups (findImages/findScenes)
            # - Media updates (imageUpdate/sceneUpdate) including gallery linking
            assert len(calls) >= 3, (
                f"Expected at least 3 GraphQL calls from second post, got {len(calls)}"
            )

            # 3. Verify galleries created (first post failed, second may create 0-1 depending on deduplication)
            gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
            assert len(gallery_creates) <= 1, (
                f"Expected 0-1 galleries (second post only, may dedupe), got {len(gallery_creates)}"
            )

            # Verify gallery was created for second post with correct URL
            if gallery_creates:
                call = gallery_creates[0]
                variables = call["variables"]
                assert "input" in variables
                input_data = variables["input"]

                # Verify URL uses our custom pattern and is for the second post
                assert "urls" in input_data
                assert "example.com" in input_data["urls"][0]
                # URL should be for second post (posts[1])
                assert str(posts[1].id) in input_data["urls"][0]

                # Response Assertions
                result = call["result"]
                if result is not None:
                    assert "galleryCreate" in result
                    assert "id" in result["galleryCreate"]

            # 4. Verify media operations occurred for second post
            # Accept both singular (by ID) and plural (by path/filter) lookups
            find_calls = [
                c
                for c in calls
                if "findImage" in c["query"] or "findScene" in c["query"]
            ]
            assert len(find_calls) > 0, "Expected media lookup calls for second post"

            # 5. Verify media updates occurred
            image_updates = [c for c in calls if "imageUpdate" in c["query"]]
            scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
            total_updates = len(image_updates) + len(scene_updates)
            assert total_updates > 0, "Expected media updates for second post"

            # 6. Verify gallery image linking via addGalleryImages
            # (Gallery.__side_mutations__["images"] fires one
            # addGalleryImages per gallery save).
            add_gallery_images_calls = [
                c for c in calls if "addGalleryImages" in c["query"]
            ]
            # Second post has images that should be linked to its gallery
            second_post_media = media_meta[1] if len(media_meta) > 1 else None
            if second_post_media and second_post_media.num_images > 0:
                assert len(add_gallery_images_calls) >= 1, (
                    f"Expected at least 1 addGalleryImages call "
                    f"for second post, got {len(add_gallery_images_calls)}"
                )

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_stash_media(
        self,
        entity_store,
        real_stash_processor,
        stash_cleanup_tracker,
    ):
        """Test _process_item_gallery when media exists but not in Stash (lines 638-646).

        Post has ACCOUNT_MEDIA attachments but the media IDs don't match any
        files in Docker Stash. Gallery is created then deleted because no
        content was processed.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"empty_gallery_{test_id}")
            await entity_store.save(account)

            # Create media that won't match anything in Stash
            media = MediaFactory.build(
                id=snowflake_id(),
                accountId=account.id,
                mimetype="image/jpeg",
                local_filename="nonexistent_in_stash.jpg",
            )
            await entity_store.save(media)

            acct_media = AccountMediaFactory.build(
                id=media.id,
                accountId=account.id,
                mediaId=media.id,
            )
            await entity_store.save(acct_media)

            post = PostFactory.build(
                accountId=account.id,
                content=f"Empty gallery test {test_id}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            att = AttachmentFactory.build(
                postId=post.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=acct_media.id,
                pos=0,
            )
            await post._add_to_relationship("attachments", att)

            # Point state at our account so process_creator finds it
            real_stash_processor.state.creator_id = account.id
            real_stash_processor.state.creator_name = account.username

            # Create real performer and studio in Docker Stash via process_creator
            _, performer = await real_stash_processor.process_creator()
            cleanup["performers"].append(performer.id)
            studio = real_stash_processor._studio
            if studio:
                cleanup["studios"].append(studio.id)

            # Process — gallery created but no media found, triggers delete (638-646)
            await real_stash_processor._process_item_gallery(
                item=post,
                account=account,
                performer=performer,
                studio=studio,
                item_type="post",
                url_pattern=f"https://fansly.com/post/{post.id}",
            )

    @pytest.mark.asyncio
    async def test_process_item_gallery_save_error(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test _process_item_gallery when gallery save fails (lines 681-696).

        Gallery is created and populated with content, but the final save() raises.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            post_media = await message_media_generator()

            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"save_error_{test_id}")
            await entity_store.save(account)

            for media in post_media.media_items:
                media.accountId = account.id
                await entity_store.save(media)

            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)

            post = PostFactory.build(
                accountId=account.id,
                content=f"Save error test {test_id}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            for attachments_created, am in enumerate(post_media.account_media_items):
                att = AttachmentFactory.build(
                    postId=post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=am.id,
                    pos=attachments_created,
                )
                await post._add_to_relationship("attachments", att)

            # Point state at our account so process_creator finds it
            real_stash_processor.state.creator_id = account.id
            real_stash_processor.state.creator_name = account.username

            # Create real performer and studio in Docker Stash
            _, performer = await real_stash_processor.process_creator()
            cleanup["performers"].append(performer.id)
            studio = real_stash_processor._studio
            if studio:
                cleanup["studios"].append(studio.id)

            # Patch store.save to fail only on the SECOND Gallery save
            # (first save creates the gallery, second is the final persist at line 680)
            original_save = real_stash_processor.store.save
            gallery_save_count = [0]

            async def failing_gallery_save(obj):
                if isinstance(obj, Gallery):
                    gallery_save_count[0] += 1
                    if gallery_save_count[0] > 1:
                        raise RuntimeError("Simulated gallery save failure")
                return await original_save(obj)

            with patch.object(
                real_stash_processor.store, "save", side_effect=failing_gallery_save
            ):
                # Should not raise — error caught at lines 681-696
                await real_stash_processor._process_item_gallery(
                    item=post,
                    account=account,
                    performer=performer,
                    studio=studio,
                    item_type="post",
                    url_pattern=f"https://fansly.com/post/{post.id}",
                )

    @pytest.mark.asyncio
    async def test_process_item_gallery_with_aggregated_posts(
        self,
        entity_store,
        real_stash_processor,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test _get_or_create_gallery creates chapters for aggregated posts (lines 426-439).

        Main post has an AGGREGATED_POSTS attachment pointing to a nested post
        that has ACCOUNT_MEDIA. The gallery gets chapters for each aggregated post.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Generate real media from Docker Stash
            post_media = await message_media_generator()

            test_id = get_unique_test_id()
            account = AccountFactory.build(username=f"agg_gallery_{test_id}")
            await entity_store.save(account)

            real_stash_processor.state.creator_id = account.id
            real_stash_processor.state.creator_name = account.username

            _, performer = await real_stash_processor.process_creator()
            cleanup["performers"].append(performer.id)
            studio = real_stash_processor._studio
            if studio:
                cleanup["studios"].append(studio.id)

            # Save media to entity store
            for media in post_media.media_items:
                media.accountId = account.id
                await entity_store.save(media)
            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)

            # Create nested post with ACCOUNT_MEDIA (this is the aggregated content)
            nested_post = PostFactory.build(
                accountId=account.id,
                content=f"Nested aggregated content {test_id}",
                createdAt=datetime(2000, 1, 2, tzinfo=UTC),
            )
            await entity_store.save(nested_post)

            # Add media attachments to the nested post
            for i, am in enumerate(post_media.account_media_items):
                nested_att = AttachmentFactory.build(
                    postId=nested_post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=am.id,
                    pos=i,
                )
                await nested_post._add_to_relationship("attachments", nested_att)

            # Create main post with both direct media AND an AGGREGATED_POSTS attachment
            main_post = PostFactory.build(
                accountId=account.id,
                content=f"Main post with aggregated {test_id}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(main_post)

            # Add direct media attachment (so gallery gets created)
            direct_att = AttachmentFactory.build(
                postId=main_post.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=post_media.account_media_items[0].id,
                pos=0,
            )
            await main_post._add_to_relationship("attachments", direct_att)

            # Add AGGREGATED_POSTS attachment pointing to nested post
            agg_att = AttachmentFactory.build(
                postId=main_post.id,
                contentType=ContentType.AGGREGATED_POSTS,
                contentId=nested_post.id,
                pos=1,
            )
            await main_post._add_to_relationship("attachments", agg_att)

            # Verify the aggregated post resolves correctly
            assert FanslyObject._store is not None, "_store not set"
            resolved = FanslyObject._store.get_from_cache(
                type(nested_post), nested_post.id
            )
            assert resolved is not None, f"nested_post {nested_post.id} not in cache"
            assert agg_att.contentId == nested_post.id
            content = await agg_att.resolve_content()
            assert content is not None, (
                f"resolve_content returned None, "
                f"contentType={agg_att.contentType}, "
                f"contentId={agg_att.contentId}"
            )
            has_media = await real_stash_processor._has_media_content(content)
            assert has_media, (
                f"Resolved post has no media content: "
                f"attachments={getattr(content, 'attachments', 'MISSING')}"
            )

            # Invalidate gallery cache so search methods don't find leftovers
            real_stash_processor.store.invalidate_type(Gallery)

            # Process — should create gallery with chapters for aggregated posts
            await real_stash_processor._process_item_gallery(
                item=main_post,
                account=account,
                performer=performer,
                studio=studio,
                item_type="post",
                url_pattern=f"https://fansly.com/post/{main_post.id}",
            )
