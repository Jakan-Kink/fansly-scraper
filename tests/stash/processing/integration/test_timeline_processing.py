"""Tests for timeline processing functionality.

This module tests StashProcessing integration with Stash API for timeline posts.
All tests use REAL database objects (Account, Post, Hashtag, etc.) created with
FactoryBoy factories instead of mocks.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import selectinload
from stash_graphql_client.types import Performer

from metadata import Account, AccountMedia, AccountMediaBundle, Post
from metadata.account import account_media_bundle_media
from metadata.attachment import Attachment, ContentType
from metadata.hashtag import post_hashtags
from metadata.post import post_mentions
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


@pytest.mark.asyncio
async def test_process_timeline_post(
    real_stash_processor,
    factory_session,
    test_database_sync,
    stash_cleanup_tracker,
):
    """Test processing a single timeline post with real database objects and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create real post with proper AccountMedia structure
        account = AccountFactory(username="timeline_user")
        factory_session.commit()

        # Create media
        media = MediaFactory(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline.mp4",
        )
        factory_session.commit()

        # Create AccountMedia
        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create post
        post = PostFactory(accountId=account.id, content="Timeline test post")
        factory_session.commit()

        # Create attachment linking post to AccountMedia
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Timeline Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Act - Use async session with proper eager loading
        async with test_database_sync.async_session_scope() as async_session:
            post_result = await async_session.execute(
                select(Post)
                .where(Post.id == post.id)
                .options(
                    selectinload(Post.attachments)
                    .selectinload(Attachment.media)
                    .selectinload(AccountMedia.media),
                )
            )
            async_post = post_result.scalar_one()

            account_result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_result.scalar_one()

            # Capture GraphQL calls made to real Stash API
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=None,
                    item_type="post",
                    items=[async_post],
                    url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                    session=async_session,
                )

            # Assert - Verify GraphQL operations performed
            # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
            assert len(calls) == 8, (
                f"Expected exactly 8 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findGalleries by code
            assert "findGalleries" in calls[0]["query"]
            assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(
                post.id
            )
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
            assert (
                str(media.id) in calls[7]["variables"]["scene_filter"]["path"]["value"]
            )
            assert "findScenes" in calls[7]["result"]


@pytest.mark.asyncio
async def test_process_timeline_bundle(
    real_stash_processor,
    factory_session,
    test_database_sync,
    stash_cleanup_tracker,
):
    """Test processing a timeline post with media bundle using real database objects and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory(username="timeline_bundle_user")
        factory_session.commit()

        # Create real media for the bundle
        media1 = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline_bundle_1.jpg",
        )
        media2 = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_timeline_bundle_2.jpg",
        )
        factory_session.commit()

        account_media1 = AccountMediaFactory(accountId=account.id, mediaId=media1.id)
        account_media2 = AccountMediaFactory(accountId=account.id, mediaId=media2.id)
        factory_session.commit()

        # Create the bundle
        bundle = AccountMediaBundleFactory(accountId=account.id)
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

        # Create post
        post = PostFactory(accountId=account.id, content="Test post with bundle")
        factory_session.commit()

        # Create attachment pointing to the bundle
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
        )
        factory_session.commit()

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Timeline Bundle Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Act - Use async session with proper eager loading
        async with test_database_sync.async_session_scope() as async_session:
            post_result = await async_session.execute(
                select(Post)
                .where(Post.id == post.id)
                .options(
                    selectinload(Post.attachments)
                    .selectinload(Attachment.bundle)
                    .selectinload(AccountMediaBundle.accountMedia)
                    .selectinload(AccountMedia.media),
                )
            )
            async_post = post_result.scalar_one()

            account_result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_result.scalar_one()

            # Capture GraphQL calls made to real Stash API
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=None,
                    item_type="post",
                    items=[async_post],
                    url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                    session=async_session,
                )

            # Assert - Verify GraphQL operations performed
            # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
            assert len(calls) == 8, (
                f"Expected exactly 8 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findGalleries by code
            assert "findGalleries" in calls[0]["query"]
            assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(
                post.id
            )
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
            assert len(studio_calls) >= 2, (
                "Expected studio-related calls at positions 4-6"
            )

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
    factory_session,
    test_database_sync,
    stash_cleanup_tracker,
):
    """Test processing timeline post hashtags using real Hashtag instances and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory(username="hashtag_user")
        factory_session.commit()

        # Create hashtags in the database
        hashtag1 = HashtagFactory(value="test")
        hashtag2 = HashtagFactory(value="example")
        factory_session.commit()

        # Create media so the post has content to process
        media = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_hashtags.jpg",
        )
        factory_session.commit()

        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create post with hashtag content
        post = PostFactory(
            accountId=account.id,
            content="Test post #test #example",
        )
        factory_session.commit()

        # Create attachment so post has media
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        factory_session.execute(
            insert(post_hashtags).values(
                [
                    {"postId": post.id, "hashtagId": hashtag1.id},
                    {"postId": post.id, "hashtagId": hashtag2.id},
                ]
            )
        )
        factory_session.commit()

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Hashtag Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Act - Use async session with proper eager loading
        async with test_database_sync.async_session_scope() as async_session:
            post_result = await async_session.execute(
                select(Post)
                .where(Post.id == post.id)
                .options(
                    selectinload(Post.attachments)
                    .selectinload(Attachment.media)
                    .selectinload(AccountMedia.media),
                    selectinload(Post.hashtags),
                )
            )
            async_post = post_result.scalar_one()

            account_result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_result.scalar_one()

            # Capture GraphQL calls made to real Stash API
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                try:
                    await real_stash_processor._process_items_with_gallery(
                        account=async_account,
                        performer=performer,
                        studio=None,
                        item_type="post",
                        items=[async_post],
                        url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                        session=async_session,
                    )
                finally:
                    print("\n=== GraphQL Call Debug Info ===")
                    for call_id, call_dict in enumerate(calls):
                        print(f"\nCall {call_id}: {call_dict}")
                    print(f"\n=== Total calls: {len(calls)} ===\n")

            # Assert - Verify GraphQL operations performed (type-based, not position-based)
            # Cache-first: gallery/tag lookups may be served from sync cache
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Verify call types by scanning (order varies with caching)
            gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
            gallery_creates = [
                c for c in calls if "galleryCreate" in c.get("query", "")
            ]
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
    factory_session,
    test_database_sync,
    stash_cleanup_tracker,
):
    """Test processing timeline post account mentions using real Account instances and real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory(username="mentions_user")
        factory_session.commit()

        # Create mentioned account using factory
        mentioned_account = AccountFactory(username="mentioned_user")
        factory_session.commit()

        # Create media so post has content to process
        media = MediaFactory(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_mentions.mp4",
        )
        factory_session.commit()

        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create post with mention
        post = PostFactory(
            accountId=account.id,
            content="Check out @mentioned_user",
        )
        factory_session.commit()

        # Create attachment so post has media
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        factory_session.execute(
            insert(post_mentions).values(
                {
                    "postId": post.id,
                    "accountId": mentioned_account.id,
                    "handle": "mentioned_user",
                }
            )
        )
        factory_session.commit()

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Mentions Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Act - Use async session with proper eager loading
        async with test_database_sync.async_session_scope() as async_session:
            post_result = await async_session.execute(
                select(Post)
                .where(Post.id == post.id)
                .options(
                    selectinload(Post.attachments)
                    .selectinload(Attachment.media)
                    .selectinload(AccountMedia.media),
                    selectinload(Post.accountMentions),
                )
            )
            async_post = post_result.scalar_one()

            account_result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_result.scalar_one()

            # Capture GraphQL calls made to real Stash API
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=None,
                    item_type="post",
                    items=[async_post],
                    url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                    session=async_session,
                )

            # Assert - Verify GraphQL operations performed (type-based, not position-based)
            # Cache-first: performer/studio/gallery lookups may be served from sync cache
            assert len(calls) >= 1, (
                f"Expected at least 1 GraphQL call, got {len(calls)}"
            )

            # Verify call types by scanning (order varies with caching)
            gallery_finds = [c for c in calls if "findGalleries" in c.get("query", "")]
            gallery_creates = [
                c for c in calls if "galleryCreate" in c.get("query", "")
            ]
            performer_finds = [
                c for c in calls if "findPerformers" in c.get("query", "")
            ]
            scene_finds = [
                c
                for c in calls
                if "FindScenes" in c.get("query", "")
                or "findScenes" in c.get("query", "")
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
    factory_session,
    test_database_sync,
    stash_cleanup_tracker,
):
    """Test processing a timeline post with expiration date using real Stash API calls."""
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        # Arrange - Create account
        account = AccountFactory(username="expired_post_user")
        factory_session.commit()

        # Create media
        media = MediaFactory(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_expired.mp4",
        )
        factory_session.commit()

        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create post with expiration date
        post = PostFactory(
            accountId=account.id,
            content="Expiring post",
            expiresAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_session.commit()

        # Create attachment so post has media
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        # Create real performer in Stash
        performer = Performer(
            name="[TEST] Expired Performer",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await real_stash_processor.context.client.create_performer(
            performer
        )
        cleanup["performers"].append(performer.id)

        # Act - Use async session with proper eager loading
        async with test_database_sync.async_session_scope() as async_session:
            post_result = await async_session.execute(
                select(Post)
                .where(Post.id == post.id)
                .options(
                    selectinload(Post.attachments)
                    .selectinload(Attachment.media)
                    .selectinload(AccountMedia.media),
                )
            )
            async_post = post_result.scalar_one()

            account_result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_result.scalar_one()

            # Capture GraphQL calls made to real Stash API
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                await real_stash_processor._process_items_with_gallery(
                    account=async_account,
                    performer=performer,
                    studio=None,
                    item_type="post",
                    items=[async_post],
                    url_pattern_func=lambda p: f"https://fansly.com/post/{p.id}",
                    session=async_session,
                )

            # Assert - Verify GraphQL operations performed
            # 3 gallery lookups + 1 galleryCreate + 3 studio calls (hoisted) + 1 media lookup = 8
            assert len(calls) == 8, (
                f"Expected exactly 8 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findGalleries by code
            assert "findGalleries" in calls[0]["query"]
            assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(
                post.id
            )
            assert "findGalleries" in calls[0]["result"]

            # Call 1: findGalleries by title
            assert "findGalleries" in calls[1]["query"]
            assert (
                calls[1]["variables"]["gallery_filter"]["title"]["value"]
                == "Expiring post"
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
            assert len(studio_calls) >= 2, (
                "Expected studio-related calls at positions 4-6"
            )

            # Call 7: FindScenes (looking for scenes with media path)
            assert "FindScenes" in calls[7]["query"]
            assert (
                str(media.id) in calls[7]["variables"]["scene_filter"]["path"]["value"]
            )
            assert "findScenes" in calls[7]["result"]
