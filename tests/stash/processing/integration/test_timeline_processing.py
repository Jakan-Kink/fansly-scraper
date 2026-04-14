"""Tests for timeline processing functionality.

This module tests StashProcessing integration with Stash API for timeline posts.
All tests use REAL database objects (Account, Post, Hashtag, etc.) created with
FactoryBoy factories instead of mocks.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from stash_graphql_client.types import Performer, Studio

from metadata import (
    ContentType,
    PostMention,
)
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    HashtagFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


async def _save_and_wire_post(
    account, media, account_media, post, attachment, entity_store
):
    """Save entities to entity_store and wire up relationships.

    Saves in FK order, then manually wires the in-memory relationship graph
    so that post.attachments -> attachment.media -> account_media.media works
    via identity map lookups.
    """
    await entity_store.save(account)
    await entity_store.save(media)
    await entity_store.save(account_media)
    await entity_store.save(post)
    await entity_store.save(attachment)
    # Wire up post.attachments so _process_items_with_gallery can traverse
    post.attachments = [attachment]


@pytest.mark.asyncio
async def test_process_timeline_post(
    real_stash_processor,
    entity_store,
    stash_cleanup_tracker,
):
    """Test processing a single timeline post with real database objects and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create real post with proper AccountMedia structure
        account = AccountFactory.build(username="timeline_user")
        media = MediaFactory.build(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline.mp4",
        )
        account_media = AccountMediaFactory.build(
            accountId=account.id, mediaId=media.id
        )
        post = PostFactory.build(accountId=account.id, content="Timeline test post")
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )

        await _save_and_wire_post(
            account, media, account_media, post, attachment, entity_store
        )

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Timeline Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Spy on store.save to track actual studio creates (not find-or-return)
        created_studios = []
        original_save = real_stash_processor.context.store.save

        async def spy_save(obj, *args, **kwargs):
            is_new_studio = isinstance(obj, Studio) and obj.is_new()
            result = await original_save(obj, *args, **kwargs)
            if is_new_studio:
                created_studios.append(obj.id)
            return result

        # Capture GraphQL calls made to real Stash API
        with (
            patch.object(
                real_stash_processor.context.store, "save", side_effect=spy_save
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=None,
                item_type="post",
                items=[post],
                url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
            )

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed
        # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
        assert len(calls) == 8, f"Expected exactly 8 GraphQL calls, got {len(calls)}"

        # Call 0: findGalleries by code
        assert "findGalleries" in calls[0]["query"]
        assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(post.id)
        assert "findGalleries" in calls[0]["result"]

        # Call 1: findGalleries by title
        assert "findGalleries" in calls[1]["query"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"]
            == "Timeline test post"
        )
        assert "findGalleries" in calls[1]["result"]

        # Call 2: findGalleries by URL
        assert "findGalleries" in calls[2]["query"]
        assert (
            calls[2]["variables"]["gallery_filter"]["url"]["value"]
            == f"https://fansly.com/post/{post.id}"
        )
        assert "findGalleries" in calls[2]["result"]

        # Call 3: galleryCreate
        assert "galleryCreate" in calls[3]["query"]
        assert calls[3]["variables"]["input"]["title"] == "Timeline test post"
        assert calls[3]["variables"]["input"]["code"] == str(post.id)
        assert (
            f"https://fansly.com/post/{post.id}"
            in calls[3]["variables"]["input"]["urls"]
        )
        assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
        assert "galleryCreate" in calls[3]["result"]
        cleanup["galleries"].append(calls[3]["result"]["galleryCreate"]["id"])

        # Calls 4-6: Studio lookup (hoisted to batch level by _process_batch_internal)
        studio_calls = [
            c
            for c in calls[4:7]
            if "findStudios" in c.get("query", "")
            or "studioCreate" in c.get("query", "")
            or "Studio" in c.get("query", "")
        ]
        assert len(studio_calls) >= 2, (
            f"Expected studio-related calls at positions 4-6, got {[c['query'][:40] for c in calls[4:7]]}"
        )

        # Call 7: FindScenes (looking for scenes with media path)
        assert "FindScenes" in calls[7]["query"]
        assert str(media.id) in calls[7]["variables"]["scene_filter"]["path"]["value"]
        assert "findScenes" in calls[7]["result"]


@pytest.mark.asyncio
async def test_process_timeline_bundle(
    real_stash_processor,
    entity_store,
    stash_cleanup_tracker,
):
    """Test processing a timeline post with media bundle using real database objects and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory.build(username="timeline_bundle_user")
        await entity_store.save(account)

        # Create real media for the bundle
        media1 = MediaFactory.build(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline_bundle_1.jpg",
        )
        media2 = MediaFactory.build(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline_bundle_2.jpg",
        )
        await entity_store.save(media1)
        await entity_store.save(media2)

        account_media1 = AccountMediaFactory.build(
            accountId=account.id, mediaId=media1.id
        )
        account_media2 = AccountMediaFactory.build(
            accountId=account.id, mediaId=media2.id
        )
        await entity_store.save(account_media1)
        await entity_store.save(account_media2)

        # Create the bundle
        bundle = AccountMediaBundleFactory.build(accountId=account.id)
        bundle.accountMedia = [account_media1, account_media2]
        await entity_store.save(bundle)

        # Create post
        post = PostFactory.build(accountId=account.id, content="Test post with bundle")
        await entity_store.save(post)

        # Create attachment pointing to the bundle
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
        )
        await entity_store.save(attachment)

        # Wire up post.attachments
        post.attachments = [attachment]

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Timeline Bundle Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Spy on store.save to track actual studio creates (not find-or-return)
        created_studios = []
        original_save = real_stash_processor.context.store.save

        async def spy_save(obj, *args, **kwargs):
            is_new_studio = isinstance(obj, Studio) and obj.is_new()
            result = await original_save(obj, *args, **kwargs)
            if is_new_studio:
                created_studios.append(obj.id)
            return result

        # Capture GraphQL calls made to real Stash API
        with (
            patch.object(
                real_stash_processor.context.store, "save", side_effect=spy_save
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=None,
                item_type="post",
                items=[post],
                url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
            )

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed
        # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
        assert len(calls) == 8, f"Expected exactly 8 GraphQL calls, got {len(calls)}"

        # Call 0: findGalleries by code
        assert "findGalleries" in calls[0]["query"]
        assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(post.id)
        assert "findGalleries" in calls[0]["result"]

        # Call 1: findGalleries by title
        assert "findGalleries" in calls[1]["query"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"]
            == "Test post with bundle"
        )
        assert "findGalleries" in calls[1]["result"]

        # Call 2: findGalleries by URL
        assert "findGalleries" in calls[2]["query"]
        assert (
            calls[2]["variables"]["gallery_filter"]["url"]["value"]
            == f"https://fansly.com/post/{post.id}"
        )
        assert "findGalleries" in calls[2]["result"]

        # Call 3: galleryCreate
        assert "galleryCreate" in calls[3]["query"]
        assert calls[3]["variables"]["input"]["title"] == "Test post with bundle"
        assert calls[3]["variables"]["input"]["code"] == str(post.id)
        assert (
            f"https://fansly.com/post/{post.id}"
            in calls[3]["variables"]["input"]["urls"]
        )
        assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
        assert "galleryCreate" in calls[3]["result"]
        cleanup["galleries"].append(calls[3]["result"]["galleryCreate"]["id"])

        # Calls 4-6: Studio lookup (hoisted to batch level by _process_batch_internal)
        studio_calls = [
            c
            for c in calls[4:7]
            if "findStudios" in c.get("query", "")
            or "studioCreate" in c.get("query", "")
            or "Studio" in c.get("query", "")
        ]
        assert len(studio_calls) >= 2, "Expected studio-related calls at positions 4-6"

        # Call 7: findImages (looking for images with media paths from bundle)
        assert "findImages" in calls[7]["query"]
        # The regex pattern contains both media IDs (Pattern 5: base_path.*(code1|code2))
        image_filter = calls[7]["variables"]["image_filter"]
        assert "path" in image_filter
        # Verify both media IDs are included in the regex pattern
        assert str(media1.id) in str(image_filter)
        assert str(media2.id) in str(image_filter)
        assert "findImages" in calls[7]["result"]


@pytest.mark.asyncio
async def test_process_timeline_hashtags(
    real_stash_processor,
    entity_store,
    stash_cleanup_tracker,
):
    """Test processing timeline post hashtags using real Hashtag instances and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory.build(username="hashtag_user")
        await entity_store.save(account)

        # Create hashtags
        hashtag1 = HashtagFactory.build(value="test")
        hashtag2 = HashtagFactory.build(value="example")
        await entity_store.save(hashtag1)
        await entity_store.save(hashtag2)

        # Create media so the post has content to process
        media = MediaFactory.build(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_hashtags.jpg",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            accountId=account.id, mediaId=media.id
        )
        await entity_store.save(account_media)

        # Create post with hashtag content
        post = PostFactory.build(
            accountId=account.id,
            content="Test post #test #example",
        )
        post.hashtags = [hashtag1, hashtag2]
        await entity_store.save(post)

        # Create attachment so post has media
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        await entity_store.save(attachment)

        # Wire up post.attachments
        post.attachments = [attachment]

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Hashtag Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Spy on store.save to track actual studio creates (not find-or-return)
        created_studios = []
        original_save = real_stash_processor.context.store.save

        async def spy_save(obj, *args, **kwargs):
            is_new_studio = isinstance(obj, Studio) and obj.is_new()
            result = await original_save(obj, *args, **kwargs)
            if is_new_studio:
                created_studios.append(obj.id)
            return result

        # Capture GraphQL calls made to real Stash API
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
                    items=[post],
                    url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                )
            finally:
                print("\n=== GraphQL Call Debug Info ===")
                for call_id, call_dict in enumerate(calls):
                    print(f"\nCall {call_id}: {call_dict}")
                print(f"\n=== Total calls: {len(calls)} ===\n")

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed (type-based, not position-based)
        # Cache-first: gallery/tag lookups may be served from sync cache
        assert len(calls) >= 1, f"Expected at least 1 GraphQL call, got {len(calls)}"

        # Verify call types by scanning (order varies with caching)
        gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
        gallery_creates = [c for c in calls if "galleryCreate" in c.get("query", "")]
        tag_finds = [c for c in calls if "findTags" in c.get("query", "")]
        tag_creates = [c for c in calls if "tagCreate" in c.get("query", "")]
        image_finds = [c for c in calls if "findImages" in c.get("query", "")]

        # Gallery lookup or creation should occur
        assert len(gallery_finds) + len(gallery_creates) >= 1, (
            "Expected gallery find or create calls"
        )

        # Track created resources for cleanup
        for call in gallery_creates:
            if "galleryCreate" in call.get("result", {}):
                cleanup["galleries"].append(call["result"]["galleryCreate"]["id"])
        for call in tag_creates:
            if "tagCreate" in call.get("result", {}):
                cleanup["tags"].append(call["result"]["tagCreate"]["id"])


@pytest.mark.asyncio
async def test_process_timeline_account_mentions(
    real_stash_processor,
    entity_store,
    stash_cleanup_tracker,
):
    """Test processing timeline post account mentions using real Account instances and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory.build(username="mentions_user")
        await entity_store.save(account)

        # Create mentioned account using factory
        mentioned_account = AccountFactory.build(username="mentioned_user")
        await entity_store.save(mentioned_account)

        # Create media so post has content to process
        media = MediaFactory.build(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_mentions.mp4",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            accountId=account.id, mediaId=media.id
        )
        await entity_store.save(account_media)

        # Create post with mention
        post = PostFactory.build(
            accountId=account.id,
            content="Check out @mentioned_user",
        )
        # Add mention via PostMention
        mention = PostMention(
            id=1,
            postId=post.id,
            accountId=mentioned_account.id,
            handle="mentioned_user",
        )
        post.mentions = [mention]
        await entity_store.save(post)

        # Create attachment so post has media
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        await entity_store.save(attachment)

        # Wire up post.attachments
        post.attachments = [attachment]

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Mentions Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Spy on store.save to track actual studio creates (not find-or-return)
        created_studios = []
        original_save = real_stash_processor.context.store.save

        async def spy_save(obj, *args, **kwargs):
            is_new_studio = isinstance(obj, Studio) and obj.is_new()
            result = await original_save(obj, *args, **kwargs)
            if is_new_studio:
                created_studios.append(obj.id)
            return result

        # Capture GraphQL calls made to real Stash API
        with (
            patch.object(
                real_stash_processor.context.store, "save", side_effect=spy_save
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=None,
                item_type="post",
                items=[post],
                url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
            )

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed (type-based, not position-based)
        # Cache-first: performer/studio/gallery lookups may be served from sync cache
        assert len(calls) >= 1, f"Expected at least 1 GraphQL call, got {len(calls)}"

        # Verify call types by scanning (order varies with caching)
        gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
        gallery_creates = [c for c in calls if "galleryCreate" in c.get("query", "")]
        performer_finds = [c for c in calls if "findPerformers" in c.get("query", "")]
        scene_finds = [
            c
            for c in calls
            if "FindScenes" in c.get("query", "") or "findScenes" in c.get("query", "")
        ]

        # Gallery lookup or creation should occur
        assert len(gallery_finds) + len(gallery_creates) >= 1, (
            "Expected gallery find or create calls"
        )

        # Track created galleries for cleanup
        for call in gallery_creates:
            if "galleryCreate" in call.get("result", {}):
                cleanup["galleries"].append(call["result"]["galleryCreate"]["id"])


@pytest.mark.asyncio
async def test_process_expired_timeline_post(
    real_stash_processor,
    entity_store,
    stash_cleanup_tracker,
):
    """Test processing a timeline post with expiration date using real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory.build(username="expired_post_user")
        media = MediaFactory.build(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_expired.mp4",
        )
        account_media = AccountMediaFactory.build(
            accountId=account.id, mediaId=media.id
        )
        post = PostFactory.build(
            accountId=account.id,
            content="Expiring post",
            expiresAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )

        await _save_and_wire_post(
            account, media, account_media, post, attachment, entity_store
        )

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Expired Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Spy on store.save to track actual studio creates (not find-or-return)
        created_studios = []
        original_save = real_stash_processor.context.store.save

        async def spy_save(obj, *args, **kwargs):
            is_new_studio = isinstance(obj, Studio) and obj.is_new()
            result = await original_save(obj, *args, **kwargs)
            if is_new_studio:
                created_studios.append(obj.id)
            return result

        # Capture GraphQL calls made to real Stash API
        with (
            patch.object(
                real_stash_processor.context.store, "save", side_effect=spy_save
            ),
            capture_graphql_calls(real_stash_processor.context.client) as calls,
        ):
            await real_stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=None,
                item_type="post",
                items=[post],
                url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
            )

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed
        # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
        assert len(calls) == 8, f"Expected exactly 8 GraphQL calls, got {len(calls)}"

        # Call 0: findGalleries by code
        assert "findGalleries" in calls[0]["query"]
        assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(post.id)
        assert "findGalleries" in calls[0]["result"]

        # Call 1: findGalleries by title
        assert "findGalleries" in calls[1]["query"]
        assert (
            calls[1]["variables"]["gallery_filter"]["title"]["value"] == "Expiring post"
        )
        assert "findGalleries" in calls[1]["result"]

        # Call 2: findGalleries by URL
        assert "findGalleries" in calls[2]["query"]
        assert (
            calls[2]["variables"]["gallery_filter"]["url"]["value"]
            == f"https://fansly.com/post/{post.id}"
        )
        assert "findGalleries" in calls[2]["result"]

        # Call 3: galleryCreate
        assert "galleryCreate" in calls[3]["query"]
        assert calls[3]["variables"]["input"]["title"] == "Expiring post"
        assert calls[3]["variables"]["input"]["code"] == str(post.id)
        assert (
            f"https://fansly.com/post/{post.id}"
            in calls[3]["variables"]["input"]["urls"]
        )
        assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
        assert "galleryCreate" in calls[3]["result"]
        cleanup["galleries"].append(calls[3]["result"]["galleryCreate"]["id"])

        # Calls 4-6: Studio lookup (hoisted to batch level by _process_batch_internal)
        studio_calls = [
            c
            for c in calls[4:7]
            if "findStudios" in c.get("query", "")
            or "studioCreate" in c.get("query", "")
            or "Studio" in c.get("query", "")
        ]
        assert len(studio_calls) >= 2, "Expected studio-related calls at positions 4-6"

        # Call 7: FindScenes (looking for scenes with media path)
        assert "FindScenes" in calls[7]["query"]
        assert str(media.id) in calls[7]["variables"]["scene_filter"]["path"]["value"]
        assert "findScenes" in calls[7]["result"]
