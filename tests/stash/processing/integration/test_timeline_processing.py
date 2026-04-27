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
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
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
        # 3 gallery lookups + 1 populate() filter-query + 1 galleryCreate
        # + 3 studio calls (hoisted) + 1 media lookup = 9
        assert len(calls) == 9, f"Expected exactly 9 GraphQL calls, got {len(calls)}"

        post_url = f"https://fansly.com/post/{post.id}"

        # Calls 0-2: gallery lookups (code/title/url)
        assert_op_with_vars(
            calls[0],
            "findGalleries",
            gallery_filter__code__value=str(post.id),
        )
        assert_op_with_vars(
            calls[1],
            "findGalleries",
            gallery_filter__title__value="Timeline test post",
        )
        assert_op_with_vars(
            calls[2],
            "findGalleries",
            gallery_filter__url__value=post_url,
        )

        # Call 3: findGalleries (populate() filter-query for performers relationship)
        # SGC v0.12 inlines filter values into the query string (no variables)
        assert_op(calls[3], "findGalleries")
        assert "performers" in calls[3]["query"]

        # Call 4: galleryCreate (composition assertion stays inline — heterogeneous fields)
        assert_op_with_vars(
            calls[4],
            "galleryCreate",
            input__title="Timeline test post",
            input__code=str(post.id),
        )
        assert post_url in calls[4]["variables"]["input"]["urls"]
        assert performer.id in calls[4]["variables"]["input"]["performer_ids"]
        cleanup["galleries"].append(calls[4]["result"]["galleryCreate"]["id"])

        # Calls 5-7: media-batch dispatch GraphQL queries (intentionally not
        # pinned by op-name — the structural len(calls) == 9 above already
        # guarantees the count; the previous "Studio" substring filter was
        # tautological because Stash response fragments include studio { id }
        # sub-selections).

        # Call 8: FindScenes (looking for scenes with media path)
        assert_op(calls[8], "FindScenes")
        assert str(media.id) in calls[8]["variables"]["scene_filter"]["path"]["value"]
        assert "findScenes" in calls[8]["result"]


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
        # 3 gallery lookups + 1 populate() filter-query + 1 galleryCreate
        # + 3 studio calls (hoisted) + 1 media lookup = 9
        assert len(calls) == 9, f"Expected exactly 9 GraphQL calls, got {len(calls)}"

        post_url = f"https://fansly.com/post/{post.id}"

        # Calls 0-2: gallery lookups (code/title/url)
        assert_op_with_vars(
            calls[0],
            "findGalleries",
            gallery_filter__code__value=str(post.id),
        )
        assert_op_with_vars(
            calls[1],
            "findGalleries",
            gallery_filter__title__value="Test post with bundle",
        )
        assert_op_with_vars(
            calls[2],
            "findGalleries",
            gallery_filter__url__value=post_url,
        )

        # Call 3: findGalleries (populate() filter-query for performers relationship)
        assert_op(calls[3], "findGalleries")
        assert "performers" in calls[3]["query"]

        # Call 4: galleryCreate (composition assertion stays inline — heterogeneous fields)
        assert_op_with_vars(
            calls[4],
            "galleryCreate",
            input__title="Test post with bundle",
            input__code=str(post.id),
        )
        assert post_url in calls[4]["variables"]["input"]["urls"]
        assert performer.id in calls[4]["variables"]["input"]["performer_ids"]
        cleanup["galleries"].append(calls[4]["result"]["galleryCreate"]["id"])

        # Calls 5-7: media-batch dispatch GraphQL queries (intentionally not
        # pinned by op-name — see structural len(calls) == 9 above).

        # Call 8: findImages (looking for images with media paths from bundle)
        # Production op name is `FindImages` (PascalCase) per the GraphQL query.
        assert_op(calls[8], "FindImages")
        # The regex pattern contains both media IDs (Pattern 5: base_path.*(code1|code2))
        image_filter = calls[8]["variables"]["image_filter"]
        assert "path" in image_filter
        # Verify both media IDs are included in the regex pattern
        assert str(media1.id) in str(image_filter)
        assert str(media2.id) in str(image_filter)
        assert "findImages" in calls[8]["result"]


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
                dump_graphql_calls(calls, "test_process_timeline_hashtags")

        # Manual studio cleanup from spy
        for sid in created_studios:
            cleanup["studios"].append(sid)

        # Assert - Verify GraphQL operations performed (type-based, not position-based)
        assert len(calls) == 17, f"Expected exactly 17 GraphQL calls, got {len(calls)}"

        # Verify call types by scanning (order varies with caching)
        gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
        gallery_creates = [c for c in calls if "galleryCreate" in c.get("query", "")]
        tag_finds = [c for c in calls if "findTags" in c.get("query", "")]
        tag_creates = [c for c in calls if "tagCreate" in c.get("query", "")]
        image_finds = [c for c in calls if "findImages" in c.get("query", "")]

        # Gallery operations: 4 lookups (code/title/url/populate) + 2 tag-based finds
        # for hashtag-galleries cross-reference + 1 galleryCreate = 7 total
        assert len(gallery_finds) + len(gallery_creates) == 7, (
            f"Expected exactly 7 gallery find/create calls, got {len(gallery_finds) + len(gallery_creates)}"
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
        assert len(calls) == 10, f"Expected exactly 10 GraphQL calls, got {len(calls)}"

        # Verify call types by scanning (order varies with caching)
        gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
        gallery_creates = [c for c in calls if "galleryCreate" in c.get("query", "")]
        performer_finds = [c for c in calls if "findPerformers" in c.get("query", "")]
        scene_finds = [
            c
            for c in calls
            if "FindScenes" in c.get("query", "") or "findScenes" in c.get("query", "")
        ]

        # Gallery operations: 4 lookups (code/title/url/populate) + 1 galleryCreate
        assert len(gallery_finds) + len(gallery_creates) == 5, (
            f"Expected exactly 5 gallery find/create calls, "
            f"got {len(gallery_finds) + len(gallery_creates)}"
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
        # 3 gallery lookups + 1 populate() filter-query + 1 galleryCreate
        # + 3 studio calls (hoisted) + 1 media lookup = 9
        assert len(calls) == 9, f"Expected exactly 9 GraphQL calls, got {len(calls)}"

        post_url = f"https://fansly.com/post/{post.id}"

        # Calls 0-2: gallery lookups (code/title/url)
        assert_op_with_vars(
            calls[0],
            "findGalleries",
            gallery_filter__code__value=str(post.id),
        )
        assert_op_with_vars(
            calls[1],
            "findGalleries",
            gallery_filter__title__value="Expiring post",
        )
        assert_op_with_vars(
            calls[2],
            "findGalleries",
            gallery_filter__url__value=post_url,
        )

        # Call 3: findGalleries (populate() filter-query for performers relationship)
        assert_op(calls[3], "findGalleries")
        assert "performers" in calls[3]["query"]

        # Call 4: galleryCreate (composition assertion stays inline — heterogeneous fields)
        assert_op_with_vars(
            calls[4],
            "galleryCreate",
            input__title="Expiring post",
            input__code=str(post.id),
        )
        assert post_url in calls[4]["variables"]["input"]["urls"]
        assert performer.id in calls[4]["variables"]["input"]["performer_ids"]
        cleanup["galleries"].append(calls[4]["result"]["galleryCreate"]["id"])

        # Calls 5-7: media-batch dispatch GraphQL queries (intentionally not
        # pinned by op-name — see structural len(calls) == 9 above).

        # Call 8: FindScenes (looking for scenes with media path)
        assert_op(calls[8], "FindScenes")
        assert str(media.id) in calls[8]["variables"]["scene_filter"]["path"]["value"]
        assert "findScenes" in calls[8]["result"]
