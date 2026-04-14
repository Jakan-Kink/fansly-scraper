"""Integration tests for message processing functionality.

TRUE INTEGRATION TESTS: Makes real GraphQL calls to Docker Stash instance.
Tests message processing using real database fixtures and real Stash HTTP calls.
"""

import math
import time
from unittest.mock import patch

import pytest
from stash_graphql_client.types import Gallery, Performer, Studio

from metadata import (
    ContentType,
)
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


async def _get_or_create_performer(client, account, cleanup):
    """Find or create a performer in Stash for the given account."""
    performer_result = await client.find_performers(
        performer_filter={"name": {"value": account.username, "modifier": "EQUALS"}}
    )
    if performer_result and performer_result.count > 0:
        return performer_result.performers[0]

    test_performer = Performer(
        name=account.username,
        urls=[f"https://fansly.com/{account.username}"],
    )
    performer = await client.create_performer(test_performer)
    cleanup["performers"].append(performer.id)
    return performer


async def _get_network_studio(client):
    """Find the Fansly network studio in Stash."""
    studio_result = await client.find_studios(q="Fansly (network)")
    if studio_result and studio_result.count > 0:
        studio_item = studio_result.studios[0]
        if isinstance(studio_item, dict):
            return Studio(**studio_item)
        return studio_item
    return None


@pytest.mark.asyncio
async def test_process_message_with_media(
    entity_store,
    real_stash_processor,
    stash_cleanup_tracker,
    message_media_generator,
):
    """Test processing a message with media attachments using real Stash integration."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        unique_id = int(time.time() * 1000) % 1000000

        # Get realistic media from Docker Stash
        media_meta = await message_media_generator()

        # Create account
        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_sender_media_{unique_id}",
        )
        await entity_store.save(account)

        # Create group
        group = GroupFactory.build(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        await entity_store.save(group)

        # Save media items with correct accountId
        for media_item in media_meta.media_items:
            media_item.accountId = account.id
            await entity_store.save(media_item)

        # Save AccountMedia with correct accountId
        for account_media_item in media_meta.account_media_items:
            account_media_item.accountId = account.id
            await entity_store.save(account_media_item)

        # Save bundle if present
        if media_meta.bundle:
            media_meta.bundle.accountId = account.id
            # Wire up bundle's accountMedia list
            bundle_am_ids = [
                link["account_media"].id for link in media_meta.bundle_media_links
            ]
            media_meta.bundle.accountMedia = [
                am for am in media_meta.account_media_items if am.id in bundle_am_ids
            ]
            await entity_store.save(media_meta.bundle)

        # Create message
        message = MessageFactory.build(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        await entity_store.save(message)

        # Create attachments
        attachment_id_offset = 700000000000000000 + unique_id
        attachments = []
        if media_meta.bundle:
            attachment = AttachmentFactory.build(
                id=attachment_id_offset,
                messageId=message.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                contentId=media_meta.bundle.id,
            )
            await entity_store.save(attachment)
            attachments.append(attachment)
            attachment_id_offset += 1

            # Videos not in bundle get individual attachments
            bundle_am_ids_set = {
                link["account_media"].id for link in media_meta.bundle_media_links
            }
            for am in media_meta.account_media_items:
                if am.id not in bundle_am_ids_set:
                    attachment = AttachmentFactory.build(
                        id=attachment_id_offset,
                        messageId=message.id,
                        contentType=ContentType.ACCOUNT_MEDIA,
                        contentId=am.id,
                    )
                    await entity_store.save(attachment)
                    attachments.append(attachment)
                    attachment_id_offset += 1
        else:
            for am in media_meta.account_media_items:
                attachment = AttachmentFactory.build(
                    id=attachment_id_offset,
                    messageId=message.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=am.id,
                )
                await entity_store.save(attachment)
                attachments.append(attachment)
                attachment_id_offset += 1

        # Wire up message.attachments
        message.attachments = attachments

        # Get performer and studio
        performer = await _get_or_create_performer(
            real_stash_processor.context.client, account, cleanup
        )
        studio = await _get_network_studio(real_stash_processor.context.client)

        # Spy on internal methods
        collected_media_from_attachments = []
        collected_media_by_mimetype = []
        collected_media_batches = []

        original_collect_media = real_stash_processor._collect_media_from_attachments
        original_process_by_mimetype = (
            real_stash_processor._process_media_batch_by_mimetype
        )
        original_process_batch = real_stash_processor._process_batch_internal

        async def spy_collect_media(spy_attachments):
            result = await original_collect_media(spy_attachments)
            collected_media_from_attachments.append(
                {
                    "count": len(result),
                    "mimetypes": [m.mimetype for m in result],
                    "media_ids": [m.id for m in result],
                }
            )
            return result

        async def spy_process_by_mimetype(media_list, item, account):
            collected_media_by_mimetype.append(
                {
                    "count": len(media_list),
                    "mimetypes": [m.mimetype for m in media_list],
                    "media_ids": [m.id for m in media_list],
                }
            )
            return await original_process_by_mimetype(media_list, item, account)

        async def spy_process_batch(media_list, item, account):
            collected_media_batches.append(
                {
                    "count": len(media_list),
                    "mimetypes": [m.mimetype for m in media_list],
                    "media_ids": [m.id for m in media_list],
                }
            )
            return await original_process_batch(media_list, item, account)

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
            patch.object(
                real_stash_processor.context.store, "save", side_effect=spy_save
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=[message],
                url_pattern_func=lambda m: (
                    f"https://fansly.com/messages/{m.groupId}/{m.id}"
                ),
            )

        # Manual cleanup from spies
        for sid in created_studios:
            cleanup["studios"].append(sid)
        for gid in created_galleries:
            cleanup["galleries"].append(gid)

        # Assert - Verify GraphQL calls
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

        individual_image_calls = images_with_stash_id
        individual_video_calls = videos_with_stash_id
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

        base_calls = 4
        media_lookup_calls = (
            individual_image_calls
            + individual_video_calls
            + bulk_image_calls
            + bulk_video_calls
        )
        performer_studio_calls = 0

        expected_min = base_calls + media_lookup_calls + performer_studio_calls
        assert len(calls) >= expected_min, (
            f"Expected at least {expected_min} GraphQL calls (base operations), "
            f"got {len(calls)}: {[c.get('query', '')[:50] for c in calls]}"
        )

        # Call 0: findGalleries (by code)
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0

        # Call 1: findGalleries (by title)
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"] == message.content
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0

        # Call 2: findGalleries (by url)
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert calls[2]["variables"]["gallery_filter"]["url"]["value"] == expected_url
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0

        # Call 3: galleryCreate
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
        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None

        # Calls 4+: findImages/findScenes
        find_calls = calls[4:]
        has_find_images = False
        has_find_scenes = False

        for call in find_calls:
            if "findImages" in call["query"]:
                assert "image_filter" in call["variables"]
                assert (
                    "OR" in call["variables"]["image_filter"]
                    or "path" in call["variables"]["image_filter"]
                )
                has_find_images = True
            elif "findScenes" in call["query"]:
                assert "scene_filter" in call["variables"]
                assert (
                    "OR" in call["variables"]["scene_filter"]
                    or "path" in call["variables"]["scene_filter"]
                )
                has_find_scenes = True

        if media_meta.num_images > 0:
            assert has_find_images, "Expected findImages call for images"


@pytest.mark.asyncio
async def test_process_message_with_bundle(
    entity_store, real_stash_processor, stash_cleanup_tracker
):
    """Test processing a message with media bundle."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        unique_id = int(time.time() * 1000) % 1000000 + 100000

        from tests.fixtures.metadata.metadata_factories import AccountMediaBundleFactory

        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_sender_bundle_{unique_id}",
        )
        await entity_store.save(account)

        group = GroupFactory.build(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        await entity_store.save(group)

        media1 = MediaFactory.build(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"/stash/media/test_bundle_{unique_id}_1.jpg",
        )
        media2 = MediaFactory.build(
            id=200000000000000001 + unique_id,
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"/stash/media/test_bundle_{unique_id}_2.jpg",
        )
        await entity_store.save(media1)
        await entity_store.save(media2)

        account_media1 = AccountMediaFactory.build(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media1.id,
        )
        account_media2 = AccountMediaFactory.build(
            id=300000000000000001 + unique_id,
            accountId=account.id,
            mediaId=media2.id,
        )
        await entity_store.save(account_media1)
        await entity_store.save(account_media2)

        bundle = AccountMediaBundleFactory.build(
            id=600000000000000000 + unique_id,
            accountId=account.id,
        )
        bundle.accountMedia = [account_media1, account_media2]
        await entity_store.save(bundle)

        message = MessageFactory.build(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        await entity_store.save(message)

        attachment = AttachmentFactory.build(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
        )
        await entity_store.save(attachment)

        # Wire up message.attachments
        message.attachments = [attachment]

        performer = await _get_or_create_performer(
            real_stash_processor.context.client, account, cleanup
        )
        studio = await _get_network_studio(real_stash_processor.context.client)

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
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=[message],
                url_pattern_func=lambda m: (
                    f"https://fansly.com/messages/{m.groupId}/{m.id}"
                ),
            )

        # Manual cleanup from spies
        for sid in created_studios:
            cleanup["studios"].append(sid)
        for gid in created_galleries:
            cleanup["galleries"].append(gid)

        assert len(calls) >= 4, (
            f"Expected at least 4 GraphQL calls (base gallery operations), got {len(calls)}"
        )

        # Call 0: findGalleries (by code)
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0

        # Call 1: findGalleries (by title)
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            message.content == calls[1]["variables"]["gallery_filter"]["title"]["value"]
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0

        # Call 2: findGalleries (by url)
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert expected_url == calls[2]["variables"]["gallery_filter"]["url"]["value"]
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0

        # Call 3: galleryCreate
        assert "galleryCreate" in calls[3]["query"]
        assert str(message.id) == calls[3]["variables"]["input"]["code"]
        assert expected_url in calls[3]["variables"]["input"]["urls"]
        assert calls[3]["variables"]["input"]["organized"] is True
        assert str(studio.id) == calls[3]["variables"]["input"]["studio_id"]
        assert "galleryCreate" in calls[3]["result"]
        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None

        if len(calls) > 4:
            find_images_calls = [c for c in calls if "findImages" in c["query"]]
            if find_images_calls:
                assert len(find_images_calls) >= 1


@pytest.mark.asyncio
async def test_process_message_with_variants(
    entity_store, real_stash_processor, stash_cleanup_tracker
):
    """Test processing a message with media variants (HLS)."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        unique_id = int(time.time() * 1000) % 1000000 + 200000

        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_sender_variants_{unique_id}",
        )
        await entity_store.save(account)

        group = GroupFactory.build(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        await entity_store.save(group)

        media = MediaFactory.build(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="application/vnd.apple.mpegurl",
            type=302,
            is_downloaded=True,
            local_filename=f"/stash/media/test_variants_{unique_id}.m3u8",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media.id,
        )
        await entity_store.save(account_media)

        message = MessageFactory.build(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        await entity_store.save(message)

        attachment = AttachmentFactory.build(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        await entity_store.save(attachment)

        # Wire up message.attachments
        message.attachments = [attachment]

        performer = await _get_or_create_performer(
            real_stash_processor.context.client, account, cleanup
        )
        studio = await _get_network_studio(real_stash_processor.context.client)

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
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=[message],
                url_pattern_func=lambda m: (
                    f"https://fansly.com/messages/{m.groupId}/{m.id}"
                ),
            )

        # Manual cleanup from spies
        for sid in created_studios:
            cleanup["studios"].append(sid)
        for gid in created_galleries:
            cleanup["galleries"].append(gid)

        assert len(calls) >= 5, f"Expected at least 5 GraphQL calls, got {len(calls)}"

        # Call 0: findGalleries (by code)
        assert "findGalleries" in calls[0]["query"]
        assert "code" in calls[0]["variables"]["gallery_filter"]
        assert (
            str(message.id) == calls[0]["variables"]["gallery_filter"]["code"]["value"]
        )
        assert "findGalleries" in calls[0]["result"]
        assert calls[0]["result"]["findGalleries"]["count"] == 0

        # Call 1: findGalleries (by title)
        assert "findGalleries" in calls[1]["query"]
        assert "title" in calls[1]["variables"]["gallery_filter"]
        assert (
            message.content == calls[1]["variables"]["gallery_filter"]["title"]["value"]
        )
        assert "findGalleries" in calls[1]["result"]
        assert calls[1]["result"]["findGalleries"]["count"] == 0

        # Call 2: findGalleries (by url)
        assert "findGalleries" in calls[2]["query"]
        assert "url" in calls[2]["variables"]["gallery_filter"]
        expected_url = f"https://fansly.com/messages/{group.id}/{message.id}"
        assert expected_url == calls[2]["variables"]["gallery_filter"]["url"]["value"]
        assert "findGalleries" in calls[2]["result"]
        assert calls[2]["result"]["findGalleries"]["count"] == 0

        # Call 3: galleryCreate
        assert "galleryCreate" in calls[3]["query"]
        assert str(message.id) == calls[3]["variables"]["input"]["code"]
        assert expected_url == calls[3]["variables"]["input"]["urls"][0]
        assert calls[3]["variables"]["input"]["organized"] is True
        assert str(studio.id) == calls[3]["variables"]["input"]["studio_id"]
        assert "galleryCreate" in calls[3]["result"]

        # Find the FindScenes call
        scene_calls = [
            c
            for c in calls[4:]
            if "FindScenes" in c.get("query", "") or "findScenes" in c.get("query", "")
        ]
        assert len(scene_calls) >= 1, (
            f"Expected at least 1 FindScenes call after gallery creation, "
            f"got calls: {[c['query'][:40] for c in calls[4:]]}"
        )
        scene_call = scene_calls[0]
        assert str(media.id) in scene_call["variables"]["scene_filter"]["path"]["value"]
        assert scene_call["result"]["findScenes"]["count"] == 0

        created_gallery_id = calls[3]["result"]["galleryCreate"]["id"]
        assert created_gallery_id is not None


@pytest.mark.asyncio
async def test_process_message_batch(
    entity_store,
    real_stash_processor,
    stash_cleanup_tracker,
    message_media_generator,
):
    """Test processing a batch of messages with parallel gallery creation."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        unique_id = int(time.time() * 1000) % 1000000 + 300000

        media_meta = await message_media_generator()

        total_media = media_meta.num_images + media_meta.num_videos
        if total_media < 3:
            pytest.skip(
                f"Not enough media generated for batch test (got {total_media}, need 3+)"
            )

        # Create account
        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_sender_batch_{unique_id}",
        )
        await entity_store.save(account)

        # Create group
        group = GroupFactory.build(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        await entity_store.save(group)

        # Save media items
        for media_item in media_meta.media_items:
            media_item.accountId = account.id
            await entity_store.save(media_item)

        # Save AccountMedia
        for account_media_item in media_meta.account_media_items:
            account_media_item.accountId = account.id
            await entity_store.save(account_media_item)

        # Create 3 messages, each with one media attachment
        messages = []
        message_id_offset = 500000000000000000 + unique_id
        attachment_id_offset = 700000000000000000 + unique_id
        for i in range(3):
            msg = MessageFactory.build(
                id=message_id_offset,
                senderId=account.id,
                groupId=group.id,
            )
            await entity_store.save(msg)
            message_id_offset += 1

            att = AttachmentFactory.build(
                id=attachment_id_offset,
                messageId=msg.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=media_meta.account_media_items[i].id,
            )
            await entity_store.save(att)
            attachment_id_offset += 1

            # Wire up message.attachments
            msg.attachments = [att]
            messages.append(msg)

        performer = await _get_or_create_performer(
            real_stash_processor.context.client, account, cleanup
        )
        studio = await _get_network_studio(real_stash_processor.context.client)

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
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=messages,
                url_pattern_func=lambda m: (
                    f"https://fansly.com/messages/{m.groupId}/{m.id}"
                ),
            )

        # Manual cleanup from spies
        for sid in created_studios:
            cleanup["studios"].append(sid)
        for gid in created_galleries:
            cleanup["galleries"].append(gid)

        assert len(calls) >= 12, (
            f"Expected at least 12 GraphQL calls (base gallery operations), got {len(calls)}"
        )

        # Verify first message gallery operations
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

        gallery_creates = [c for c in calls if "galleryCreate" in c["query"]]
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
    entity_store, real_stash_processor, stash_cleanup_tracker
):
    """Test error handling during message processing."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        unique_id = int(time.time() * 1000) % 1000000 + 400000

        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_sender_error_{unique_id}",
        )
        await entity_store.save(account)

        group = GroupFactory.build(
            id=400000000000000000 + unique_id,
            createdBy=account.id,
        )
        await entity_store.save(group)

        media = MediaFactory.build(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=False,
            stash_id=None,
            local_filename=f"/nonexistent/path/test_error_{unique_id}.mp4",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            id=300000000000000000 + unique_id,
            accountId=account.id,
            mediaId=media.id,
        )
        await entity_store.save(account_media)

        message = MessageFactory.build(
            id=500000000000000000 + unique_id,
            senderId=account.id,
            groupId=group.id,
        )
        await entity_store.save(message)

        attachment = AttachmentFactory.build(
            id=700000000000000000 + unique_id,
            messageId=message.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        await entity_store.save(attachment)

        # Wire up message.attachments
        message.attachments = [attachment]

        performer = await _get_or_create_performer(
            real_stash_processor.context.client, account, cleanup
        )
        studio = await _get_network_studio(real_stash_processor.context.client)

        # Act - Process and expect graceful error handling
        no_exception_raised = True
        try:
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=[message],
                url_pattern_func=lambda m: (
                    f"https://fansly.com/messages/{m.groupId}/{m.id}"
                ),
            )
        except Exception:
            no_exception_raised = False

        assert no_exception_raised, "Processing should handle errors gracefully"
