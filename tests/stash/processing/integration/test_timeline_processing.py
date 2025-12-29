"""Tests for timeline processing functionality.

This module tests StashProcessing integration with Stash API for timeline posts.
All tests use REAL database objects (Account, Post, Hashtag, etc.) created with
FactoryBoy factories instead of mocks.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import selectinload

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
from tests.fixtures.stash.stash_type_factories import PerformerFactory


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
        performer = PerformerFactory.build(
            id="new",  # to_input() converts this properly
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
            assert len(calls) == 5, (
                f"Expected exactly 5 GraphQL calls, got {len(calls)}"
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
                calls[3]["variables"]["input"]["url"]
                == f"https://fansly.com/post/{post.id}"
            )
            assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
            assert "galleryCreate" in calls[3]["result"]

            # Call 4: findScenes (looking for scenes with media path)
            assert "findScenes" in calls[4]["query"]
            assert (
                str(media.id) in calls[4]["variables"]["scene_filter"]["path"]["value"]
            )
            assert "findScenes" in calls[4]["result"]


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
        performer = PerformerFactory.build(
            id="new",  # to_input() converts this properly
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
            assert len(calls) == 5, (
                f"Expected exactly 5 GraphQL calls, got {len(calls)}"
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
                calls[3]["variables"]["input"]["url"]
                == f"https://fansly.com/post/{post.id}"
            )
            assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
            assert "galleryCreate" in calls[3]["result"]

            # Call 4: findImages (looking for images with media paths from bundle)
            assert "findImages" in calls[4]["query"]
            # The OR filter contains paths for both media items in the bundle
            image_filter = calls[4]["variables"]["image_filter"]
            assert "OR" in image_filter
            # Verify both media IDs are included in the path filters
            assert str(media1.id) in str(image_filter) or str(media2.id) in str(
                image_filter
            )
            assert "findImages" in calls[4]["result"]


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
        performer = PerformerFactory.build(
            id="new",  # to_input() converts this properly
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
            assert len(calls) == 11, (
                f"Expected exactly 11 GraphQL calls (3 gallery find + 1 create + 2 hashtags x 3 calls each + 1 image find), got {len(calls)}"
            )

            # Calls 0-2: findGalleries (by code, title, URL)
            assert "findGalleries" in calls[0]["query"]
            assert "findGalleries" in calls[1]["query"]
            assert "findGalleries" in calls[2]["query"]

            # Call 3: galleryCreate
            assert "galleryCreate" in calls[3]["query"]
            assert "Test post #test #example" in calls[3]["variables"]["input"]["title"]

            # Calls 4-6: Process first hashtag "test"
            assert "findTags" in calls[4]["query"]
            assert calls[4]["variables"]["tag_filter"]["name"]["value"] == "test"

            assert "findTags" in calls[5]["query"]
            assert calls[5]["variables"]["tag_filter"]["aliases"]["value"] == "test"

            assert "tagCreate" in calls[6]["query"]
            assert calls[6]["variables"]["input"]["name"] == "test"

            # Calls 7-9: Process second hashtag "example"
            assert "findTags" in calls[7]["query"]
            assert calls[7]["variables"]["tag_filter"]["name"]["value"] == "example"

            assert "findTags" in calls[8]["query"]
            assert calls[8]["variables"]["tag_filter"]["aliases"]["value"] == "example"

            assert "tagCreate" in calls[9]["query"]
            assert calls[9]["variables"]["input"]["name"] == "example"

            # Call 10: findImages for media
            assert "findImages" in calls[10]["query"]
            assert str(media.id) in str(calls[10]["variables"]["image_filter"])


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
        performer = PerformerFactory.build(
            id="new",  # to_input() converts this properly
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

            # Assert - Verify GraphQL operations performed
            assert len(calls) == 7, (
                f"Expected exactly 7 GraphQL calls (3 gallery find + 2 performer find + 1 create + 1 scene find), got {len(calls)}"
            )

            # Calls 0-2: findGalleries (by code, title, URL)
            assert "findGalleries" in calls[0]["query"]
            assert calls[0]["variables"]["gallery_filter"]["code"]["value"] == str(
                post.id
            )

            assert "findGalleries" in calls[1]["query"]
            assert (
                calls[1]["variables"]["gallery_filter"]["title"]["value"]
                == "Check out @mentioned_user"
            )

            assert "findGalleries" in calls[2]["query"]
            assert (
                calls[2]["variables"]["gallery_filter"]["url"]["value"]
                == f"https://fansly.com/post/{post.id}"
            )

            # Calls 3-4: Find mentioned performer
            assert "findPerformers" in calls[3]["query"]
            assert (
                calls[3]["variables"]["performer_filter"]["name"]["value"]
                == "mentioned_user"
            )

            assert "findPerformers" in calls[4]["query"]
            assert (
                calls[4]["variables"]["performer_filter"]["aliases"]["value"]
                == "mentioned_user"
            )

            # Call 5: galleryCreate
            assert "galleryCreate" in calls[5]["query"]
            assert (
                calls[5]["variables"]["input"]["title"] == "Check out @mentioned_user"
            )
            assert performer.id in calls[5]["variables"]["input"]["performer_ids"]

            # Call 6: findScenes for video media
            assert "findScenes" in calls[6]["query"]
            assert str(media.id) in str(calls[6]["variables"]["scene_filter"])


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
        performer = PerformerFactory.build(
            id="new",  # to_input() converts this properly
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
            assert len(calls) == 5, (
                f"Expected exactly 5 GraphQL calls, got {len(calls)}"
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
                calls[3]["variables"]["input"]["url"]
                == f"https://fansly.com/post/{post.id}"
            )
            assert performer.id in calls[3]["variables"]["input"]["performer_ids"]
            assert "galleryCreate" in calls[3]["result"]

            # Call 4: findScenes (looking for scenes with media path)
            assert "findScenes" in calls[4]["query"]
            assert (
                str(media.id) in calls[4]["variables"]["scene_filter"]["path"]["value"]
            )
            assert "findScenes" in calls[4]["result"]
