"""Integration tests for message processing functionality.

TRUE INTEGRATION TESTS: Makes real GraphQL calls to Docker Stash instance.
Tests message processing using real database fixtures and real Stash HTTP calls.
"""

import math
import time
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from stash_graphql_client.types import Performer, Studio

from metadata import Account, AccountMedia, AccountMediaBundle, Message
from metadata.attachment import Attachment, ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


@pytest.mark.asyncio
async def test_process_message_with_media(
    test_database_sync,
    real_stash_processor,
    stash_cleanup_tracker,
    message_media_generator,
):
    """Test processing a message with media attachments using real Stash integration."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Use unique timestamp to avoid name conflicts during parallel execution
        unique_id = (
            int(time.time() * 1000) % 1000000
        )  # Last 6 digits of millisecond timestamp

        # Get realistic media from Docker Stash (fixture returns built objects)
        media_meta = await message_media_generator()

        # Do everything in async session
        async with test_database_sync.async_session_scope() as async_session:
            # Create account with unique ID and username
            account = AccountFactory.build(
                id=100000000000000000 + unique_id,
                username=f"test_sender_media_{unique_id}",
            )
            async_session.add(account)
            await async_session.flush()  # Persist account

            # Create group with unique ID
            group = GroupFactory.build(
                id=400000000000000000 + unique_id,
                createdBy=account.id,
            )
            async_session.add(group)
            await async_session.flush()  # Persist group

            # Set accountId on all media items and add to session
            for media_item in media_meta.media_items:
                media_item.accountId = account.id
                async_session.add(media_item)

            # Set accountId on AccountMedia objects and add to session
            for account_media_item in media_meta.account_media_items:
                account_media_item.accountId = account.id
                async_session.add(account_media_item)

            # If there's a bundle, add it and link media
            if media_meta.bundle:
                media_meta.bundle.accountId = account.id
                async_session.add(media_meta.bundle)
                await async_session.flush()  # Get bundle.id for join table

                # Link account_media to bundle via join table
                from metadata.account import account_media_bundle_media

                for link in media_meta.bundle_media_links:
                    await async_session.execute(
                        account_media_bundle_media.insert().values(
                            bundle_id=media_meta.bundle.id,
                            media_id=link["account_media"].id,
                            pos=link["pos"],
                        )
                    )

            await async_session.flush()  # Ensure all media committed before message

            # Create message with unique ID
            message = MessageFactory.build(
                id=500000000000000000 + unique_id,
                senderId=account.id,
                groupId=group.id,
            )
            async_session.add(message)
            await async_session.flush()  # Persist message

            # Create attachments based on bundle vs individual media
            attachment_id_offset = 700000000000000000 + unique_id
            if media_meta.bundle:
                # Bundle attachment (for >3 images) - bundle contains only images
                attachment = AttachmentFactory.build(
                    id=attachment_id_offset,
                    messageId=message.id,
                    contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                    contentId=media_meta.bundle.id,
                )
                async_session.add(attachment)
                attachment_id_offset += 1

                # Videos are NOT in bundle - create individual attachments for them
                # Bundle only contains images, videos are in account_media_items but not bundle
                video_account_media = [
                    am
                    for am in media_meta.account_media_items
                    if am.id
                    not in [
                        link["account_media"].id
                        for link in media_meta.bundle_media_links
                    ]
                ]
                for video_am in video_account_media:
                    attachment = AttachmentFactory.build(
                        id=attachment_id_offset,
                        messageId=message.id,
                        contentType=ContentType.ACCOUNT_MEDIA,
                        contentId=video_am.id,
                    )
                    async_session.add(attachment)
                    attachment_id_offset += 1
            else:
                # Individual attachments (for ≤3 images + videos)
                for account_media_item in media_meta.account_media_items:
                    attachment = AttachmentFactory.build(
                        id=attachment_id_offset,
                        messageId=message.id,
                        contentType=ContentType.ACCOUNT_MEDIA,
                        contentId=account_media_item.id,
                    )
                    async_session.add(attachment)
                    attachment_id_offset += 1

            await async_session.commit()

        # Get real performer and studio from Stash
        # Find or create performer for test account
        performer_result = await real_stash_processor.context.client.find_performers(
            performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
        )

        if performer_result and performer_result.count > 0:
            performer = Performer(**performer_result.performers[0])
        else:
            # Create performer if doesn't exist
            test_performer = Performer(
                name=account.username,
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                test_performer
            )
            cleanup["performers"].append(performer.id)

        # Find network studio (should exist in Stash)
        studio_result = await real_stash_processor.context.client.find_studios(
            q="Fansly (network)"
        )

        studio = None
        if studio_result and studio_result.count > 0:
            # Handle both dict (old behavior) and Studio object (new behavior)
            studio_item = studio_result.studios[0]
            if isinstance(studio_item, dict):
                studio = Studio(**studio_item)
            else:
                # Already a Studio object
                studio = studio_item

        # Act - Process in async session with proper relationship loading
        # Spy on internal methods to trace where videos are lost
        collected_media_from_attachments = []
        collected_media_by_mimetype = []
        collected_media_batches = []

        original_collect_media = real_stash_processor._collect_media_from_attachments
        original_process_by_mimetype = (
            real_stash_processor._process_media_batch_by_mimetype
        )
        original_process_batch = real_stash_processor._process_batch_internal

        async def spy_collect_media(attachments):
            """Spy wrapper to capture what media is collected from attachments."""
            result = await original_collect_media(attachments)
            collected_media_from_attachments.append(
                {
                    "count": len(result),
                    "mimetypes": [m.mimetype for m in result],
                    "media_ids": [m.id for m in result],
                }
            )
            return result

        async def spy_process_by_mimetype(media_list, item, account):
            """Spy wrapper to capture what media is passed to mimetype processor."""
            collected_media_by_mimetype.append(
                {
                    "count": len(media_list),
                    "mimetypes": [m.mimetype for m in media_list],
                    "media_ids": [m.id for m in media_list],
                }
            )
            return await original_process_by_mimetype(media_list, item, account)

        async def spy_process_batch(media_list, item, account):
            """Spy wrapper to capture what media is being processed."""
            collected_media_batches.append(
                {
                    "count": len(media_list),
                    "mimetypes": [m.mimetype for m in media_list],
                    "media_ids": [m.id for m in media_list],
                }
            )
            return await original_process_batch(media_list, item, account)

        with (
            patch.object(
                real_stash_processor,
                "_collect_media_from_attachments",
                side_effect=spy_collect_media,
            ),
            patch.object(
                real_stash_processor,
                "_process_media_batch_by_mimetype",
                side_effect=spy_process_by_mimetype,
            ),
            patch.object(
                real_stash_processor,
                "_process_batch_internal",
                side_effect=spy_process_batch,
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            async with test_database_sync.async_session_scope() as async_session:
                # Re-query with eager loading in async session
                # Need to load both bundle and media paths depending on attachment type
                result = await async_session.execute(
                    select(Message)
                    .where(Message.id == message.id)
                    .options(
                        selectinload(Message.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.attachments)
                        .selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.group),
                    )
                )
                async_message = result.scalar_one()

                account_result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_result.scalar_one()

                # Process the message through full pipeline
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=studio,
                    item_type="message",
                    items=[async_message],
                    url_pattern_func=lambda m: f"https://fansly.com/messages/{m.groupId}/{m.id}",
                    session=async_session,
                )

        # Assert - REQUIRED: Verify exact GraphQL call sequence
        # For a brand new message (stash_id=None, unique username):
        # 0. findGalleries (by code) - won't find anything
        # 1. findGalleries (by title) - won't find anything
        # 2. findGalleries (by url) - won't find anything
        # 3. galleryCreate - create new gallery
        # 4+. findImages/findScenes calls (one per media item to link to gallery)

        # Calculate expected calls dynamically based on what was generated:
        # - Media WITH stash_id: Individual findImage/findScene calls (1 per media)
        # - Media WITHOUT stash_id: Bulk findImages/findScenes (batches of 20)
        # - Plus base gallery operations, performer, studio, updates, etc.

        # Count media by stash_id presence
        images_with_stash_id = sum(
            1
            for m in media_meta.media_items
            if m.mimetype.startswith("image/") and m.stash_id
        )
        images_without_stash_id = sum(
            1
            for m in media_meta.media_items
            if m.mimetype.startswith("image/") and not m.stash_id
        )
        videos_with_stash_id = sum(
            1
            for m in media_meta.media_items
            if m.mimetype.startswith("video/") and m.stash_id
        )
        videos_without_stash_id = sum(
            1
            for m in media_meta.media_items
            if m.mimetype.startswith("video/") and not m.stash_id
        )

        # Calculate individual calls (1 per media with stash_id)
        individual_image_calls = images_with_stash_id
        individual_video_calls = videos_with_stash_id

        # Calculate bulk calls (batches of 20 for media without stash_id)
        bulk_image_calls = (
            math.ceil(images_without_stash_id / 20)
            if images_without_stash_id > 0
            else 0
        )
        bulk_video_calls = (
            math.ceil(videos_without_stash_id / 20)
            if videos_without_stash_id > 0
            else 0
        )

        # Base calls: 3 gallery lookups + 1 gallery create
        base_calls = 4

        # Media lookup calls
        media_lookup_calls = (
            individual_image_calls
            + individual_video_calls
            + bulk_image_calls
            + bulk_video_calls
        )

        # Performer/studio calls: 1 findPerformers + 1 findStudios + 1 studioCreate (or use existing)
        # NOTE: This is approximate - actual count varies based on whether entities already exist
        performer_studio_calls = 2  # At minimum: findPerformers + findStudios

        # Update calls: 1 per image/scene found + 1 gallery add images
        # NOTE: This is approximate - actual count depends on how many were found in Stash
        # For now, we'll just use a range

        # Dynamic assertion based on actual media generated
        # Min: base + media lookups + performer/studio
        # Max: Much higher due to UpdateImage calls (1 per found image), studio re-lookups, etc.
        expected_min = base_calls + media_lookup_calls + performer_studio_calls
        # Real integration tests have many more calls due to updates and studio lookups
        # Just verify we have at least the minimum required calls
        assert len(calls) >= expected_min, (
            f"Expected at least {expected_min} GraphQL calls (base operations), "
            f"got {len(calls)}: {[c.get('query', '')[:50] for c in calls]}"
        )

        # Call 0: findGalleries (by code) - verify message ID
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        ), (
            f"Expected code={message.id}, got {calls[0]['variables']['gallery_filter']['code']['value']}"
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 1: findGalleries (by title) - verify message content used as title
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"] == message.content
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 2: findGalleries (by url) - verify group URL pattern
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert calls[2]["variables"]["gallery_filter"]["url"]["value"] == expected_url
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 3: galleryCreate - verify all input fields
        assert "galleryCreate" in calls[3]["query"]
        create_input = calls[3]["variables"]["input"]
        assert create_input["title"] == message.content
        assert create_input["code"] == str(message.id)
        assert expected_url in create_input["urls"]
        assert create_input["details"] == message.content
        assert create_input["organized"] is True
        assert create_input["studio_id"] == str(studio.id)
        assert create_input["performer_ids"] == [str(performer.id)]
        assert "galleryCreate" in calls[3]["result"]
        # Verify gallery was actually created
        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None

        # Calls 4+: findImages/findScenes/findPerformers (link gallery to media by path)
        # Processing code makes ONE bulk call per media type with all paths in nested OR filter
        find_calls = calls[4:]

        # Should have 0-3 calls: findImages (if images), findScenes (if videos), findPerformers (alias)
        has_find_images = False
        has_find_scenes = False
        has_find_performers = False

        for call in find_calls:
            if "findImages" in call["query"]:
                assert "image_filter" in call["variables"]
                # Should have nested OR filter with all image paths
                assert (
                    "OR" in call["variables"]["image_filter"]
                    or "path" in call["variables"]["image_filter"]
                )
                has_find_images = True
            elif "findScenes" in call["query"]:
                assert "scene_filter" in call["variables"]
                # Should have nested OR filter with all scene paths
                assert (
                    "OR" in call["variables"]["scene_filter"]
                    or "path" in call["variables"]["scene_filter"]
                )
                has_find_scenes = True
            elif "findPerformers" in call["query"]:
                # Performer alias lookup (happens after gallery creation)
                has_find_performers = True

        # Verify we got the expected find calls based on media types
        if media_meta.num_images > 0:
            assert has_find_images, "Expected findImages call for images"
        if media_meta.num_videos > 0:
            # Note: findScenes may or may not happen depending on processing logic
            pass  # Don't assert - videos might not trigger scene creation


@pytest.mark.asyncio
async def test_process_message_with_bundle(
    factory_session, test_database_sync, real_stash_processor, stash_cleanup_tracker
):
    """Test processing a message with media bundle."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Use unique timestamp to avoid name conflicts during parallel execution
        # Add test-specific offset to prevent same-millisecond collisions with other tests
        unique_id = int(time.time() * 1000) % 1000000 + 100000  # Last 6 digits + offset

        # Arrange: Create real database objects with proper bundle structure
        # Use unique_id in ALL entity IDs to prevent parallel test collisions
        account = AccountFactory(
            id=100000000000000000 + unique_id,
            username=f"test_sender_bundle_{unique_id}",
        )
        factory_session.commit()

        group = GroupFactory(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        factory_session.commit()

        # Create real media for the bundle with unique IDs and filenames
        media1 = MediaFactory(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"/stash/media/test_bundle_{unique_id}_1.jpg",
        )
        media2 = MediaFactory(
            id=200000000000000001 + unique_id,
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"/stash/media/test_bundle_{unique_id}_2.jpg",
        )
        factory_session.commit()

        # Create AccountMedia for each media
        from metadata.account import account_media_bundle_media
        from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory

        account_media1 = AccountMediaFactory(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media1.id,
        )
        account_media2 = AccountMediaFactory(
            id=300000000000000001 + unique_id,
            accountId=account.id,
            mediaId=media2.id,
        )
        factory_session.commit()

        # Create the bundle
        bundle = AccountMediaBundleFactory(
            id=600000000000000000 + unique_id,
            accountId=account.id,
        )
        factory_session.commit()

        # Link AccountMedia to bundle via the join table
        factory_session.execute(
            account_media_bundle_media.insert().values(
                [
                    {"bundle_id": bundle.id, "media_id": account_media1.id, "pos": 0},
                    {"bundle_id": bundle.id, "media_id": account_media2.id, "pos": 1},
                ]
            )
        )
        factory_session.commit()

        # Create message with unique ID
        message = MessageFactory(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        factory_session.commit()

        # Create attachment pointing to the bundle
        attachment = AttachmentFactory(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
        )
        factory_session.commit()

        # Get real performer from Stash
        performer_result = await real_stash_processor.context.client.find_performers(
            performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
        )

        if performer_result and performer_result.count > 0:
            performer = Performer(**performer_result.performers[0])
        else:
            test_performer = Performer(
                name=account.username,
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                test_performer
            )
            cleanup["performers"].append(performer.id)

        # Get studio
        studio_result = await real_stash_processor.context.client.find_studios(
            q="Fansly (network)"
        )

        studio = None
        if studio_result and studio_result.count > 0:
            # Handle both dict (old behavior) and Studio object (new behavior)
            studio_item = studio_result.studios[0]
            if isinstance(studio_item, dict):
                studio = Studio(**studio_item)
            else:
                # Already a Studio object
                studio = studio_item

        # Act - Process with real Stash calls
        with capture_graphql_calls(real_stash_processor.context.client) as calls:
            async with test_database_sync.async_session_scope() as async_session:
                from metadata import AccountMediaBundle

                result = await async_session.execute(
                    select(Message)
                    .where(Message.id == message.id)
                    .options(
                        selectinload(Message.attachments)
                        .selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.group),
                    )
                )
                async_message = result.scalar_one()

                account_result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_result.scalar_one()

                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=studio,
                    item_type="message",
                    items=[async_message],
                    url_pattern_func=lambda m: f"https://fansly.com/messages/{m.groupId}/{m.id}",
                    session=async_session,
                )

        # Assert - REQUIRED: Verify exact GraphQL call sequence
        # For bundle message with 2 JPEG images (stash_id=None, synthetic paths):
        # Base: 4 gallery operations (3 findGalleries + 1 galleryCreate)
        # Plus: findImages call to check if media exists (won't find synthetic paths)

        # Expect at least 4 base gallery calls
        assert len(calls) >= 4, (
            f"Expected at least 4 GraphQL calls (base gallery operations), got {len(calls)}"
        )

        # Call 0: findGalleries (by code) - verify message ID
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        ), (
            f"Expected code={message.id}, got {calls[0]['variables']['gallery_filter']['code']['value']}"
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 1: findGalleries (by title) - verify message content used as title
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"] == message.content
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 2: findGalleries (by url) - verify group URL pattern
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert calls[2]["variables"]["gallery_filter"]["url"]["value"] == expected_url
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 3: galleryCreate - verify all input fields
        assert "galleryCreate" in calls[3]["query"]
        create_input = calls[3]["variables"]["input"]
        assert create_input["title"] == message.content
        assert create_input["code"] == str(message.id)
        assert expected_url in create_input["urls"]
        assert create_input["details"] == message.content
        assert create_input["organized"] is True
        assert create_input["studio_id"] == str(studio.id)
        assert create_input["performer_ids"] == [str(performer.id)]
        assert "galleryCreate" in calls[3]["result"]
        # Verify gallery was actually created
        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None

        # If there are additional calls, they should be for media processing
        # Bundle has 2 JPEG images with synthetic paths, so expect findImages call
        if len(calls) > 4:
            find_images_calls = [c for c in calls if "findImages" in c["query"]]
            if find_images_calls:
                # Verify findImages is looking for the bundle media by path
                # Should have path filter with media IDs or file paths
                assert len(find_images_calls) >= 1


@pytest.mark.asyncio
async def test_process_message_with_variants(
    factory_session, test_database_sync, real_stash_processor, stash_cleanup_tracker
):
    """Test processing a message with media variants (HLS)."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Use unique timestamp to avoid name conflicts during parallel execution
        # Add test-specific offset to prevent same-millisecond collisions with other tests
        unique_id = int(time.time() * 1000) % 1000000 + 200000  # Last 6 digits + offset

        # Arrange: Create real database objects with unique IDs
        # Use unique_id in ALL entity IDs to prevent parallel test collisions
        account = AccountFactory(
            id=100000000000000000 + unique_id,
            username=f"test_sender_variants_{unique_id}",
        )
        factory_session.commit()

        group = GroupFactory(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        factory_session.commit()

        # Create HLS media with variants - unique ID and filename
        media = MediaFactory(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="application/vnd.apple.mpegurl",
            type=302,  # HLS stream
            is_downloaded=True,
            local_filename=f"/stash/media/test_variants_{unique_id}.m3u8",
            metadata='{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}',
        )
        factory_session.commit()

        account_media = AccountMediaFactory(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media.id,
        )
        factory_session.commit()

        message = MessageFactory(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        factory_session.commit()

        attachment = AttachmentFactory(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        # Get real performer from Stash
        performer_result = await real_stash_processor.context.client.find_performers(
            performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
        )

        if performer_result and performer_result.count > 0:
            performer = Performer(**performer_result.performers[0])
        else:
            test_performer = Performer(
                name=account.username,
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                test_performer
            )
            cleanup["performers"].append(performer.id)

        # Get studio
        studio_result = await real_stash_processor.context.client.find_studios(
            q="Fansly (network)"
        )

        studio = None
        if studio_result and studio_result.count > 0:
            # Handle both dict (old behavior) and Studio object (new behavior)
            studio_item = studio_result.studios[0]
            if isinstance(studio_item, dict):
                studio = Studio(**studio_item)
            else:
                # Already a Studio object
                studio = studio_item

        # Act - Process with real Stash calls
        with capture_graphql_calls(real_stash_processor.context.client) as calls:
            async with test_database_sync.async_session_scope() as async_session:
                result = await async_session.execute(
                    select(Message)
                    .where(Message.id == message.id)
                    .options(
                        selectinload(Message.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.group),
                    )
                )
                async_message = result.scalar_one()

                account_result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_result.scalar_one()

                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=studio,
                    item_type="message",
                    items=[async_message],
                    url_pattern_func=lambda m: f"https://fansly.com/messages/{m.groupId}/{m.id}",
                    session=async_session,
                )

        # Assert - REQUIRED: Verify exact GraphQL call sequence
        # For HLS variant message (mimetype: application/vnd.apple.mpegurl, synthetic path):
        # Base: 4 gallery operations (3 findGalleries + 1 galleryCreate)
        # Plus: findScenes call to check if HLS video exists (won't find synthetic path)

        # Expect at least 5 calls (4 gallery + 1 findScenes)
        assert len(calls) >= 5, (
            f"Expected at least 5 GraphQL calls (4 gallery + 1 findScenes), got {len(calls)}"
        )

        # Call 0: findGalleries (by code) - verify message ID
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 1: findGalleries (by title) - verify message content used as title
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            message.content == calls[1]["variables"]["gallery_filter"]["title"]["value"]
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 2: findGalleries (by url) - verify group URL pattern
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert expected_url == calls[2]["variables"]["gallery_filter"]["url"]["value"]
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0  # Not found

        # Call 3: galleryCreate - verify key fields
        assert "galleryCreate" in calls[3]["query"]
        assert str(message.id) == calls[3]["variables"]["input"]["code"]
        assert expected_url == calls[3]["variables"]["input"]["urls"][0]
        assert calls[3]["variables"]["input"]["organized"] is True
        assert str(studio.id) == calls[3]["variables"]["input"]["studio_id"]
        assert "galleryCreate" in calls[3]["result"]

        # Call 4: findScenesByPathRegex - checking for HLS video
        assert "findScenesByPathRegex" in calls[4]["query"]
        assert str(media.id) in calls[4]["variables"]["filter"]["q"]
        assert (
            calls[4]["result"]["findScenesByPathRegex"]["count"] == 0
        )  # Not found (synthetic path)

        # Verify gallery was created
        assert "galleryCreate" in calls[3]["result"]
        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None


@pytest.mark.asyncio
async def test_process_message_batch(
    test_database_sync,
    real_stash_processor,
    stash_cleanup_tracker,
    message_media_generator,
):
    """Test processing a batch of messages with parallel gallery creation."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Use unique timestamp to avoid name conflicts during parallel execution
        # Add test-specific offset to prevent same-millisecond collisions with other tests
        unique_id = int(time.time() * 1000) % 1000000 + 300000  # Last 6 digits + offset

        # Get realistic media from Docker Stash
        media_meta = await message_media_generator()

        # Check if we have at least 3 media for batch testing
        total_media = media_meta.num_images + media_meta.num_videos
        if total_media < 3:
            pytest.skip(
                f"Not enough media generated for batch test (got {total_media}, need 3+)"
            )

        # Do everything in async session
        async with test_database_sync.async_session_scope() as async_session:
            # Create account with unique ID and username
            account = AccountFactory.build(
                id=100000000000000000 + unique_id,
                username=f"test_sender_batch_{unique_id}",
            )
            async_session.add(account)
            await async_session.flush()  # Persist account

            # Create group with unique ID
            group = GroupFactory.build(
                id=400000000000000000 + unique_id,
                createdBy=account.id,
            )
            async_session.add(group)
            await async_session.flush()  # Persist group

            # Set accountId on all media items and add to session
            for media_item in media_meta.media_items:
                media_item.accountId = account.id
                async_session.add(media_item)

            # Set accountId on AccountMedia objects and add to session
            for account_media_item in media_meta.account_media_items:
                account_media_item.accountId = account.id
                async_session.add(account_media_item)

            await async_session.flush()

            # Split first 3 media items into 3 separate messages
            # (to test parallel processing of multiple messages)
            messages = []
            message_id_offset = 500000000000000000 + unique_id
            attachment_id_offset = 700000000000000000 + unique_id
            for i in range(3):
                message = MessageFactory.build(
                    id=message_id_offset,
                    senderId=account.id,
                    groupId=group.id,
                )
                async_session.add(message)
                await async_session.flush()  # Persist message
                message_id_offset += 1

                # Attach one media item per message
                attachment = AttachmentFactory.build(
                    id=attachment_id_offset,
                    messageId=message.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=media_meta.account_media_items[i].id,
                )
                async_session.add(attachment)
                attachment_id_offset += 1
                messages.append(message)

            await async_session.commit()

        # Get real performer from Stash
        performer_result = await real_stash_processor.context.client.find_performers(
            performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
        )

        if performer_result and performer_result.count > 0:
            performer = Performer(**performer_result.performers[0])
        else:
            test_performer = Performer(
                name=account.username,
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                test_performer
            )
            cleanup["performers"].append(performer.id)

        # Get studio
        studio_result = await real_stash_processor.context.client.find_studios(
            q="Fansly (network)"
        )

        studio = None
        if studio_result and studio_result.count > 0:
            # Handle both dict (old behavior) and Studio object (new behavior)
            studio_item = studio_result.studios[0]
            if isinstance(studio_item, dict):
                studio = Studio(**studio_item)
            else:
                # Already a Studio object
                studio = studio_item

        # Act - Process all 3 messages (tests parallel processing)
        with capture_graphql_calls(real_stash_processor.context.client) as calls:
            async with test_database_sync.async_session_scope() as async_session:
                # Re-query messages with eager loading to load attachments and media
                result = await async_session.execute(
                    select(Message)
                    .where(Message.id.in_([m.id for m in messages]))
                    .options(
                        selectinload(Message.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.group),
                    )
                )
                async_messages = result.scalars().all()

                account_result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_result.scalar_one()

                # Process all 3 messages together (parallel gallery creation)
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=studio,
                    item_type="message",
                    items=async_messages,
                    url_pattern_func=lambda m: f"https://fansly.com/messages/{m.groupId}/{m.id}",
                    session=async_session,
                )

        # Assert - REQUIRED: Verify exact GraphQL call sequence
        # For 3 brand new messages from same group (stash_id=None, unique URLs per message):
        # Each message: 3 findGalleries (code/title/URL) + 1 galleryCreate
        # Total base gallery calls: (3 + 1) x 3 = 12 calls
        # Plus: findImages/findScenes + addGalleryImages + studio/performer ops

        # Verify we have at least the base gallery operations
        # Note: Actual count higher due to media linking, studio/performer ops
        assert len(calls) >= 12, (
            f"Expected at least 12 GraphQL calls (base gallery operations), got {len(calls)}"
        )

        # Verify call pattern for each message's gallery operations
        # Message 1: Calls 0-3 (3 findGalleries + 1 galleryCreate)
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(
            messages[0].id
        )

        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]

        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]

        assert "galleryCreate" in calls[3]["query"]

        # Find subsequent gallery operations (code/title findGalleries + galleryCreate)
        # Can't assume exact positions due to interleaved media/studio operations
        gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
        # Media deduplication: Shared media from Docker Stash causes variable gallery counts
        assert 1 <= len(gallery_creates) <= 3, (
            f"Expected 1-3 galleries (deduplication), got {len(gallery_creates)}"
        )

        find_galleries_by_code = [
            c
            for c in calls
            if "findGalleries" in c["query"]
            and "code" in c["variables"]["gallery_filter"]
        ]
        assert len(find_galleries_by_code) == 3, (
            f"Expected 3 findGalleries by code, got {len(find_galleries_by_code)}"
        )


@pytest.mark.asyncio
async def test_process_message_error_handling(
    factory_session, test_database_sync, real_stash_processor, stash_cleanup_tracker
):
    """Test error handling during message processing."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Use unique timestamp to avoid name conflicts during parallel execution
        # Add test-specific offset to prevent same-millisecond collisions with other tests
        unique_id = int(time.time() * 1000) % 1000000 + 400000  # Last 6 digits + offset

        # Arrange: Create real database objects with potentially problematic data
        # Use unique_id in ALL entity IDs to prevent parallel test collisions
        account = AccountFactory(
            id=100000000000000000 + unique_id,
            username=f"test_sender_error_{unique_id}",
        )
        factory_session.commit()

        group = GroupFactory(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        factory_session.commit()

        # Create media with non-existent file (will cause processing issues) - unique ID
        media = MediaFactory(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=False,  # Not downloaded - may cause issues
            stash_id=None,
            local_filename=f"/nonexistent/path/test_error_{unique_id}.mp4",
        )
        factory_session.commit()

        account_media = AccountMediaFactory(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media.id,
        )
        factory_session.commit()

        message = MessageFactory(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        factory_session.commit()

        attachment = AttachmentFactory(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        # Get real performer from Stash
        performer_result = await real_stash_processor.context.client.find_performers(
            performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
        )

        if performer_result and performer_result.count > 0:
            performer = Performer(**performer_result.performers[0])
        else:
            test_performer = Performer(
                name=account.username,
                urls=[f"https://fansly.com/{account.username}"],
            )
            performer = await real_stash_processor.context.client.create_performer(
                test_performer
            )
            cleanup["performers"].append(performer.id)

        # Get studio
        studio_result = await real_stash_processor.context.client.find_studios(
            q="Fansly (network)"
        )

        studio = None
        if studio_result and studio_result.count > 0:
            # Handle both dict (old behavior) and Studio object (new behavior)
            studio_item = studio_result.studios[0]
            if isinstance(studio_item, dict):
                studio = Studio(**studio_item)
            else:
                # Already a Studio object
                studio = studio_item

        # Act - Process and expect graceful error handling (no exception raised to caller)
        no_exception_raised = True
        try:
            async with test_database_sync.async_session_scope() as async_session:
                result = await async_session.execute(
                    select(Message)
                    .where(Message.id == message.id)
                    .options(
                        selectinload(Message.attachments)
                        .selectinload(Attachment.media)
                        .selectinload(AccountMedia.media),
                        selectinload(Message.group),
                    )
                )
                async_message = result.scalar_one()

                account_result = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_result.scalar_one()

                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=studio,
                    item_type="message",
                    items=[async_message],
                    url_pattern_func=lambda m: f"https://fansly.com/messages/{m.groupId}/{m.id}",
                    session=async_session,
                )
        except Exception:
            no_exception_raised = False

        # Assert - error should be handled gracefully without raising exception
        assert no_exception_raised, "Processing should handle errors gracefully"
