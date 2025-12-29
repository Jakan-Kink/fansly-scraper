"""Integration tests for post and message processing in StashProcessing.

This module tests content processing (posts and messages) using real database
fixtures and factory-based test data. All tests use REAL Stash API calls verified
with capture_graphql_calls.
"""

from functools import wraps
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account, AccountMedia, Post
from metadata.account import account_media_bundle_media
from metadata.attachment import Attachment, ContentType
from metadata.messages import group_users
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AttachmentFactory,
    GroupFactory,
    MessageFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.stash.stash_type_factories import PerformerFactory
from tests.fixtures.utils.test_isolation import get_unique_test_id


class TestContentProcessingIntegration:
    """Integration tests for content processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator_posts_integration(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test process_creator_posts with real database and Stash API integration."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            media_meta = await message_media_generator(spread_over_objs=3)

            test_id = get_unique_test_id()
            account = AccountFactory(username=f"post_creator_{test_id}")
            factory_session.commit()

            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]

                for media in post_media.media_items:
                    media.accountId = account.id
                    factory_session.add(media)
                factory_session.commit()

                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    factory_session.add(account_media)
                factory_session.commit()

                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    factory_session.add(post_media.bundle)
                    factory_session.commit()

                    for link_data in post_media.bundle_media_links:
                        factory_session.execute(
                            account_media_bundle_media.insert().values(
                                bundle_id=post_media.bundle.id,
                                media_id=link_data["account_media"].id,
                                pos=link_data["pos"],
                            )
                        )
                    factory_session.commit()

                post = PostFactory(accountId=account.id, content=f"Post {i}")
                factory_session.commit()

                # Attach media to post (bundle + individual, mimics real Fansly API behavior)
                attachments_created = 0

                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    factory_session.add(attachment)
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

                        attachment = AttachmentFactory(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        factory_session.add(attachment)
                        attachments_created += 1

                posts.append(post)
            factory_session.commit()

            performer = PerformerFactory.build(
                id="new",
                name="[TEST] Post Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            async with test_database_sync.async_session_scope() as async_session:
                result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = result.scalar_one()

                # SPY: Validate media lookup routing (by path vs by ID)
                lookup_routing = {"by_path": 0, "by_id": 0}
                original_find_by_path = real_stash_processor._find_stash_files_by_path

                @wraps(original_find_by_path)
                async def spy_find_by_path(media_files):
                    lookup_routing["by_path"] += len(media_files)
                    return await original_find_by_path(media_files)

                original_find_by_id = real_stash_processor._find_stash_files_by_id

                @wraps(original_find_by_id)
                async def spy_find_by_id(stash_files, session=None):
                    lookup_routing["by_id"] += len(stash_files)
                    return await original_find_by_id(stash_files, session=session)

                # Capture GraphQL calls made to real Stash API
                with (
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
                    await real_stash_processor.process_creator_posts(
                        account=async_account,
                        performer=performer,
                        studio=None,
                        session=async_session,
                    )

                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert len(gallery_creates) == 3

                created_gallery_ids = []
                for call in gallery_creates:
                    variables = call["variables"]
                    input_data = variables["input"]
                    assert "title" in input_data
                    assert "post_creator" in input_data["title"]
                    assert input_data.get("date")
                    assert "url" in input_data
                    assert "fansly.com/post/" in input_data["url"]
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

                assert media_found_count == media_meta.total_media, (
                    f"Expected to find all {media_meta.total_media} media items, found {media_found_count}"
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

                posts_with_images = sum(1 for p in media_meta if p.num_images > 0)
                add_gallery_images = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                assert len(add_gallery_images) == posts_with_images

                for call in add_gallery_images:
                    input_data = call["variables"]["input"]
                    assert input_data["gallery_id"] in created_gallery_ids
                    assert isinstance(input_data["image_ids"], list)
                    if call["result"] is not None:
                        assert call["result"]["addGalleryImages"] is True

                # SPY: Verify media lookup routing (fast path via ID vs slow path via file)
                total_lookups = lookup_routing["by_path"] + lookup_routing["by_id"]
                assert total_lookups > 0
                assert total_lookups == media_meta.total_media

    @pytest.mark.asyncio
    async def test_process_creator_messages_integration(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test process_creator_messages with real database and Stash API integration."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            media_meta = await message_media_generator(spread_over_objs=3)

            test_id = get_unique_test_id()
            account = AccountFactory(username=f"message_creator_{test_id}")
            factory_session.commit()

            group = GroupFactory(createdBy=account.id)
            factory_session.commit()

            factory_session.execute(
                group_users.insert().values(groupId=group.id, accountId=account.id)
            )
            factory_session.commit()

            messages = []
            for i in range(len(media_meta)):
                message_media = media_meta[i]

                for media in message_media.media_items:
                    media.accountId = account.id
                    factory_session.add(media)
                factory_session.commit()

                for account_media in message_media.account_media_items:
                    account_media.accountId = account.id
                    factory_session.add(account_media)
                factory_session.commit()

                if message_media.has_bundle and message_media.bundle:
                    message_media.bundle.accountId = account.id
                    factory_session.add(message_media.bundle)
                    factory_session.commit()

                    for link_data in message_media.bundle_media_links:
                        factory_session.execute(
                            account_media_bundle_media.insert().values(
                                bundle_id=message_media.bundle.id,
                                media_id=link_data["account_media"].id,
                                pos=link_data["pos"],
                            )
                        )
                    factory_session.commit()

                message = MessageFactory(
                    groupId=group.id, senderId=account.id, content=f"Message {i}"
                )
                factory_session.commit()

                # Attach media to message (bundle + individual, mimics real Fansly API behavior)
                attachments_created = 0

                if message_media.has_bundle and message_media.bundle:
                    attachment = AttachmentFactory(
                        messageId=message.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=message_media.bundle.id,
                        pos=attachments_created,
                    )
                    factory_session.add(attachment)
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

                        attachment = AttachmentFactory(
                            messageId=message.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        factory_session.add(attachment)
                        attachments_created += 1

                messages.append(message)
            factory_session.commit()

            performer = PerformerFactory.build(
                id="new",
                name="[TEST] Message Creator",
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                performer
            )
            cleanup["performers"].append(performer.id)

            async with test_database_sync.async_session_scope() as async_session:
                result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = result.scalar_one()

                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    await real_stash_processor.process_creator_messages(
                        account=async_account,
                        performer=performer,
                        studio=None,
                        session=async_session,
                    )

                # Permanent GraphQL Call Assertions

                # 1. Verify Gallery Creation
                # Expected: 3 galleries created (one for each message)
                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert len(gallery_creates) == 3, (
                    f"Expected 3 galleryCreate calls (one per message), got {len(gallery_creates)}"
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

                assert media_found_count == media_meta.total_media, (
                    f"Expected to find all {media_meta.total_media} media items, found {media_found_count}"
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

                # 4. Verify Gallery Image Addition
                # Count how many messages actually have images (vs only scenes)
                messages_with_images = sum(
                    1 for message_media in media_meta if message_media.num_images > 0
                )

                # Expected: addGalleryImages called for each message with images
                add_gallery_images = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                assert len(add_gallery_images) == messages_with_images, (
                    f"Expected {messages_with_images} addGalleryImages calls "
                    f"(one per message with images), got {len(add_gallery_images)}"
                )

                for call in add_gallery_images:
                    # Request Assertions
                    variables = call["variables"]
                    assert "input" in variables
                    input_data = variables["input"]

                    assert "gallery_id" in input_data
                    assert input_data["gallery_id"] in created_gallery_ids
                    assert "image_ids" in input_data
                    assert isinstance(input_data["image_ids"], list)

                    # Response Assertions - skip if race condition caused error
                    result = call["result"]
                    if result is not None:
                        assert "addGalleryImages" in result
                        assert result["addGalleryImages"] is True

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        message_media_generator,
        stash_cleanup_tracker,
    ):
        """Test _process_items_with_gallery integration with real posts."""
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Generate realistic media for 2 posts using Docker Stash data
            media_meta = await message_media_generator(spread_over_objs=2)

            # Create real account and posts using factories
            test_id = get_unique_test_id()
            account = AccountFactory(username=f"gallery_creator_{test_id}")
            factory_session.commit()

            # Create 2 posts, each with its own media distribution
            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]  # Get media for this specific post

                # Set accountId on media and commit
                for media in post_media.media_items:
                    media.accountId = account.id
                    factory_session.add(media)
                factory_session.commit()

                # Set accountId on AccountMedia and commit
                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    factory_session.add(account_media)
                factory_session.commit()

                # Handle bundle if present for this post
                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    factory_session.add(post_media.bundle)
                    factory_session.commit()

                    # Link AccountMedia to bundle
                    for link_data in post_media.bundle_media_links:
                        factory_session.execute(
                            account_media_bundle_media.insert().values(
                                bundle_id=post_media.bundle.id,
                                media_id=link_data["account_media"].id,
                                pos=link_data["pos"],
                            )
                        )
                    factory_session.commit()

                # Create post with factory-generated realistic content
                post = PostFactory(accountId=account.id)
                factory_session.commit()

                # Attach media to post (mimics real Fansly API)
                # Real API can have: bundle only, individual only, OR bundle + individual
                attachments_created = 0

                # First: Add bundle attachment if present
                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    factory_session.add(attachment)
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
                        attachment = AttachmentFactory(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        factory_session.add(attachment)
                        attachments_created += 1

                posts.append(post)
            factory_session.commit()

            # Create real performer in Stash
            performer = PerformerFactory.build(
                id="new",
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

            # Use async session from the database
            async with test_database_sync.async_session_scope() as async_session:
                # Load posts with relationships
                posts_result = await async_session.execute(
                    select(Post)
                    .where(Post.id.in_([p.id for p in posts]))
                    .options(
                        selectinload(Post.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media)
                    )
                )
                async_posts = posts_result.scalars().all()

                # Capture GraphQL calls made to real Stash API
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    await real_stash_processor._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=None,
                        item_type="post",
                        items=async_posts,
                        url_pattern_func=url_pattern_func,
                        session=async_session,
                    )

                # Permanent GraphQL Call Assertions

                # 1. Verify Gallery Creation
                # Expected: 2 galleries created (one for each post)
                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert len(gallery_creates) == 2, (
                    f"Expected 2 galleryCreate calls, got {len(gallery_creates)}"
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
                            for p in async_posts
                            if str(p.id) in input_data.get("url", "")
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
                    assert "url" in input_data
                    assert "example.com" in input_data["url"]
                    assert "performer_ids" in input_data
                    assert performer.id in input_data["performer_ids"]

                    # Response Assertions
                    result = call["result"]
                    assert "galleryCreate" in result
                    assert "id" in result["galleryCreate"]
                    created_gallery_ids.append(result["galleryCreate"]["id"])

                # Verify the URLs were generated correctly with custom pattern
                gallery_urls = [
                    c["variables"]["input"]["url"]
                    for c in gallery_creates
                    if "url" in c["variables"].get("input", {})
                ]
                assert f"https://example.com/{posts[0].id}" in gallery_urls
                assert f"https://example.com/{posts[1].id}" in gallery_urls

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

                assert media_found_count == media_meta.total_media, (
                    f"Expected to find all {media_meta.total_media} media items, found {media_found_count}"
                )

                # 3. Verify Media Updates
                image_updates = [c for c in calls if "imageUpdate" in c["query"]]
                scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
                total_updates = len(image_updates) + len(scene_updates)

                assert total_updates > 0, "Expected media updates to occur"

                # 4. Verify Gallery Image Addition
                # Count how many posts actually have images (vs only scenes)
                posts_with_images = sum(
                    1 for post_media in media_meta if post_media.num_images > 0
                )

                # Expected: addGalleryImages called for each post with images
                add_gallery_images = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                assert len(add_gallery_images) == posts_with_images, (
                    f"Expected {posts_with_images} addGalleryImages calls "
                    f"(one per post with images), got {len(add_gallery_images)}"
                )

                for call in add_gallery_images:
                    # Request Assertions
                    variables = call["variables"]
                    assert "input" in variables
                    input_data = variables["input"]

                    assert "gallery_id" in input_data
                    assert input_data["gallery_id"] in created_gallery_ids
                    assert "image_ids" in input_data
                    assert isinstance(input_data["image_ids"], list)

                    # Response Assertions - skip if race condition caused error
                    result = call["result"]
                    if result is not None:
                        assert "addGalleryImages" in result
                        assert result["addGalleryImages"] is True

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
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

            # Create real account and posts using factories
            test_id = get_unique_test_id()
            account = AccountFactory(username=f"error_creator_{test_id}")
            factory_session.commit()

            # Create 2 posts, each with its own media distribution
            posts = []
            for i in range(len(media_meta)):
                post_media = media_meta[i]  # Get media for this specific post

                # Set accountId on media and commit
                for media in post_media.media_items:
                    media.accountId = account.id
                    factory_session.add(media)
                factory_session.commit()

                # Set accountId on AccountMedia and commit
                for account_media in post_media.account_media_items:
                    account_media.accountId = account.id
                    factory_session.add(account_media)
                factory_session.commit()

                # Handle bundle if present for this post
                if post_media.has_bundle and post_media.bundle:
                    post_media.bundle.accountId = account.id
                    factory_session.add(post_media.bundle)
                    factory_session.commit()

                    # Link AccountMedia to bundle
                    for link_data in post_media.bundle_media_links:
                        factory_session.execute(
                            account_media_bundle_media.insert().values(
                                bundle_id=post_media.bundle.id,
                                media_id=link_data["account_media"].id,
                                pos=link_data["pos"],
                            )
                        )
                    factory_session.commit()

                # Create post
                post = PostFactory(accountId=account.id, content=f"Error test post {i}")
                factory_session.commit()

                # Attach media to post (mimics real Fansly API)
                # Real API can have: bundle only, individual only, OR bundle + individual
                attachments_created = 0

                # First: Add bundle attachment if present
                if post_media.has_bundle and post_media.bundle:
                    attachment = AttachmentFactory(
                        postId=post.id,
                        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                        contentId=post_media.bundle.id,
                        pos=attachments_created,
                    )
                    factory_session.add(attachment)
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
                        attachment = AttachmentFactory(
                            postId=post.id,
                            contentType=ContentType.ACCOUNT_MEDIA,
                            contentId=account_media.id,
                            pos=attachments_created,
                        )
                        factory_session.add(attachment)
                        attachments_created += 1

                posts.append(post)
            factory_session.commit()

            # Create real performer in Stash
            performer = PerformerFactory.build(
                id="new",
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

            # Use async session from the database
            async with test_database_sync.async_session_scope() as async_session:
                # Load posts with relationships
                posts_result = await async_session.execute(
                    select(Post)
                    .where(Post.id.in_([p.id for p in posts]))
                    .options(
                        selectinload(Post.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media)
                    )
                )
                async_posts = posts_result.scalars().all()

                # Capture GraphQL calls - should only see calls from second post (first fails early)
                with capture_graphql_calls(
                    real_stash_processor.context.client
                ) as calls:
                    await real_stash_processor._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=None,
                        item_type="post",
                        items=async_posts,
                        url_pattern_func=url_pattern_func,
                        session=async_session,
                    )

                # Permanent GraphQL Call Assertions for Error Handling

                # 1. Verify error recovery - both posts were attempted despite first failing
                assert call_count == 2, (
                    f"Expected 2 calls to _process_item_gallery, got {call_count}"
                )

                # 2. Verify second post still processed (first post failed, second succeeded)
                # The second post should have made GraphQL calls for:
                # - Gallery creation (1 call)
                # - Media lookups (findImages/findScenes)
                # - Media updates (imageUpdate/sceneUpdate)
                # - Gallery image addition (addGalleryImages)
                assert len(calls) >= 4, (
                    f"Expected at least 4 GraphQL calls from second post, got {len(calls)}"
                )

                # 3. Verify only ONE gallery was created (first post failed before gallery creation)
                gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
                assert len(gallery_creates) == 1, (
                    f"Expected 1 galleryCreate call (second post only), got {len(gallery_creates)}"
                )

                # Verify gallery was created for second post with correct URL
                if gallery_creates:
                    call = gallery_creates[0]
                    variables = call["variables"]
                    assert "input" in variables
                    input_data = variables["input"]

                    # Verify URL uses our custom pattern and is for the second post
                    assert "url" in input_data
                    assert "example.com" in input_data["url"]
                    # URL should be for second post (posts[1])
                    assert str(posts[1].id) in input_data["url"]

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
                assert len(find_calls) > 0, (
                    "Expected media lookup calls for second post"
                )

                # 5. Verify media updates occurred
                image_updates = [c for c in calls if "imageUpdate" in c["query"]]
                scene_updates = [c for c in calls if "sceneUpdate" in c["query"]]
                total_updates = len(image_updates) + len(scene_updates)
                assert total_updates > 0, "Expected media updates for second post"

                # 6. Verify gallery image addition occurred
                add_gallery_images = [
                    c for c in calls if "addGalleryImages" in c["query"]
                ]
                assert len(add_gallery_images) >= 1, (
                    f"Expected at least 1 addGalleryImages call, got {len(add_gallery_images)}"
                )
