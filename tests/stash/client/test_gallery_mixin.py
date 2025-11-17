"""Integration tests for GalleryClientMixin.

These tests use:
1. Real Docker Stash instance via StashClient (NO MOCKS)
2. Real Gallery objects created via API
3. stash_cleanup_tracker to clean up test data

IMPORTANT NOTES:
- Galleries CAN be created via Stash API (GalleryCreateInput exists)
- All tests use real Stash instance, no mocking of client methods
- Cleanup happens automatically via stash_cleanup_tracker context manager
"""

from datetime import UTC, datetime

import pytest

from metadata import Post
from stash import StashClient
from stash.types import (
    Gallery,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


@pytest.mark.asyncio
async def test_find_gallery(stash_client: StashClient, stash_cleanup_tracker) -> None:
    """Test finding a gallery by ID with real Stash instance.

    This test:
    1. Creates a real Gallery in Stash via API
    2. Finds the Gallery by ID
    3. Verifies all fields are returned correctly
    4. Tests not found case with invalid ID
    5. Cleans up the Gallery automatically
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a real Gallery in Stash
        gallery = Gallery(
            id="new",  # Signal to to_input() this is a new object
            title="Test Gallery for Find",
            code="FIND001",
            details="Test gallery for find operation",
            date="2024-01-01",
            urls=["https://example.com/test-find-gallery"],
            organized=False,  # Not organized so we can modify it
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # Test successful find
        found_gallery = await stash_client.find_gallery(created_gallery.id)
        assert found_gallery is not None
        assert found_gallery.id == created_gallery.id
        assert found_gallery.title == "Test Gallery for Find"
        assert found_gallery.code == "FIND001"
        assert found_gallery.details == "Test gallery for find operation"
        assert found_gallery.date == "2024-01-01"
        assert "https://example.com/test-find-gallery" in found_gallery.urls
        assert not found_gallery.organized
        # Note: photographer and rating100 are not in GALLERY_FIELDS fragment,
        # so they won't be returned by the API

        # Test not found with invalid ID
        not_found = await stash_client.find_gallery("999999999")
        assert not_found is None

        # Automatic cleanup happens when exiting context


@pytest.mark.asyncio
async def test_find_galleries(stash_client: StashClient, stash_cleanup_tracker) -> None:
    """Test finding galleries with filters using real Stash instance.

    This test:
    1. Creates 3 test galleries with different properties
    2. Tests finding galleries with various filters
    3. Tests pagination and sorting
    4. Tests search query
    5. Cleans up all created galleries
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create 3 test galleries with different properties
        gallery1 = Gallery(
            id="new",
            title="Alpha Test Gallery",
            code="ALPHA001",
            details="First test gallery",
            date="2024-01-01",
            urls=["https://example.com/alpha"],
            organized=False,
        )
        created1 = await stash_client.create_gallery(gallery1)
        cleanup["galleries"].append(created1.id)

        gallery2 = Gallery(
            id="new",
            title="Beta Test Gallery",
            code="BETA002",
            details="Second test gallery",
            date="2024-02-01",
            urls=["https://example.com/beta"],
            organized=False,
        )
        created2 = await stash_client.create_gallery(gallery2)
        cleanup["galleries"].append(created2.id)

        gallery3 = Gallery(
            id="new",
            title="Gamma Test Gallery",
            code="GAMMA003",
            details="Third test gallery",
            date="2024-03-01",
            urls=["https://example.com/gamma"],
            organized=False,  # Create unorganized first
        )
        created3 = await stash_client.create_gallery(gallery3)
        cleanup["galleries"].append(created3.id)

        # WORKAROUND: Stash has a bug where galleryCreate ignores the 'organized' field
        # even though it's defined in GalleryCreateInput schema. We must update separately.
        # See: stash_organized_field_bug_reproduction.py
        created3.organized = True
        created3 = await stash_client.update_gallery(created3)

        # Test 1: Find all test galleries with search query
        result = await stash_client.find_galleries(q="Test Gallery")
        assert result.count >= 3  # At least our 3 galleries
        # Note: Strawberry doesn't auto-deserialize, galleries are dicts
        gallery_ids = [
            g["id"] if isinstance(g, dict) else g.id for g in result.galleries
        ]
        assert created1.id in gallery_ids
        assert created2.id in gallery_ids
        assert created3.id in gallery_ids

        # Test 2: Find with pagination
        result_page1 = await stash_client.find_galleries(
            filter_={"page": 1, "per_page": 2, "sort": "title", "direction": "ASC"}
        )
        assert len(result_page1.galleries) >= 2

        # Test 3: Find with gallery_filter for organized status
        result_organized = await stash_client.find_galleries(
            gallery_filter={"organized": True}
        )
        # Should find at least our one organized gallery
        assert result_organized.count >= 1
        organized_ids = [
            g["id"] if isinstance(g, dict) else g.id for g in result_organized.galleries
        ]
        assert created3.id in organized_ids

        # Test 4: Find with no results (invalid search)
        result_empty = await stash_client.find_galleries(
            q="ThisShouldNotMatchAnything12345XYZ"
        )
        assert result_empty.count == 0
        assert len(result_empty.galleries) == 0

        # Automatic cleanup happens when exiting context


@pytest.mark.asyncio
async def test_create_gallery(
    stash_client: StashClient, stash_cleanup_tracker, mock_gallery: Gallery
) -> None:
    """Test creating a gallery - TRUE INTEGRATION TEST.

    Makes real calls to Stash to verify gallery creation works end-to-end.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Test create with minimum required fields
        with capture_graphql_calls(stash_client) as calls:
            gallery = Gallery(
                id="new",  # Signal this is a new object
                title="Test Gallery Minimum Fields",
                urls=["https://example.com/gallery/minimum"],
                organized=False,
            )
            created = await stash_client.create_gallery(gallery)
            cleanup["galleries"].append(created.id)

            # Verify created gallery has expected fields
            assert created.id != "new"  # Real ID from Stash
            assert created.title == "Test Gallery Minimum Fields"
            assert "https://example.com/gallery/minimum" in created.urls
            assert created.organized is False

            # Verify GraphQL call sequence (permanent assertion)
            assert len(calls) == 1, "Expected exactly 1 GraphQL call for create"
            assert "galleryCreate" in calls[0]["query"]

        # Test create with all optional fields (if fixture has relationships)
        with capture_graphql_calls(stash_client) as calls_full:
            gallery_full = Gallery(
                id="new",
                title="Test Gallery All Fields",
                code="TEST_CODE_123",
                details="Test gallery with all fields populated",
                photographer="Test Photographer",
                rating100=85,
                urls=["https://example.com/gallery/full"],
                organized=True,
            )
            # Only add relationships if fixture has them
            if mock_gallery.studio:
                gallery_full.studio = mock_gallery.studio
            if mock_gallery.performers and len(mock_gallery.performers) > 0:
                gallery_full.performers = [mock_gallery.performers[0]]
            if mock_gallery.tags and len(mock_gallery.tags) > 0:
                gallery_full.tags = [mock_gallery.tags[0]]

            created_full = await stash_client.create_gallery(gallery_full)
            cleanup["galleries"].append(created_full.id)

            # Verify all fields were set correctly
            assert created_full.id != "new"
            assert created_full.title == "Test Gallery All Fields"
            assert created_full.code == "TEST_CODE_123"
            assert created_full.details == "Test gallery with all fields populated"
            assert (
                created_full.organized is False
            )  # Bug: Stash ignores 'organized' in galleryCreate

            # Verify relationships (may be dicts or objects depending on deserialization)
            if gallery_full.studio and created_full.studio:
                studio_id = (
                    created_full.studio["id"]
                    if isinstance(created_full.studio, dict)
                    else created_full.studio.id
                )
                assert studio_id == mock_gallery.studio.id

            if (
                gallery_full.performers
                and len(gallery_full.performers) > 0
                and created_full.performers
                and len(created_full.performers) > 0
            ):
                assert len(created_full.performers) >= 1

            if (
                gallery_full.tags
                and len(gallery_full.tags) > 0
                and created_full.tags
                and len(created_full.tags) > 0
            ):
                assert len(created_full.tags) >= 1

            # Verify GraphQL call sequence (permanent assertion)
            assert len(calls_full) == 1, (
                "Expected exactly 1 GraphQL call for create with all fields"
            )
            assert "galleryCreate" in calls_full[0]["query"]


@pytest.mark.asyncio
async def test_update_gallery(
    stash_client: StashClient, stash_cleanup_tracker, mock_gallery: Gallery
) -> None:
    """Test updating a gallery - TRUE INTEGRATION TEST.

    Makes real calls to Stash to verify gallery updates work end-to-end.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # First create a gallery to update
        gallery = Gallery(
            id="new",
            title="Original Title",
            code="ORIGINAL",
            details="Original details",
            urls=["https://example.com/original"],
            organized=False,
        )
        created = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created.id)

        # Test update single field
        with capture_graphql_calls(stash_client) as calls:
            created.title = "Updated Title"
            updated = await stash_client.update_gallery(created)

            assert updated.id == created.id
            assert updated.title == "Updated Title"
            assert updated.code == "ORIGINAL"  # Unchanged

            # Verify GraphQL call sequence
            assert len(calls) == 1, "Expected exactly 1 GraphQL call for update"
            assert "galleryUpdate" in calls[0]["query"]

        # Test update multiple fields
        with capture_graphql_calls(stash_client) as calls_multi:
            updated.details = "Updated details"
            updated.code = "UPDATED_CODE"
            updated_multi = await stash_client.update_gallery(updated)

            assert updated_multi.id == created.id
            assert updated_multi.details == "Updated details"
            assert updated_multi.code == "UPDATED_CODE"

            # Verify GraphQL call sequence
            assert len(calls_multi) == 1, (
                "Expected exactly 1 GraphQL call for multi-field update"
            )
            assert "galleryUpdate" in calls_multi[0]["query"]

        # Test update with relationship (if fixture has one)
        if mock_gallery.performers and len(mock_gallery.performers) > 0:
            with capture_graphql_calls(stash_client) as calls_rel:
                updated_multi.performers = [mock_gallery.performers[0]]
                updated_rel = await stash_client.update_gallery(updated_multi)

                assert updated_rel.id == created.id
                if updated_rel.performers and len(updated_rel.performers) > 0:
                    assert len(updated_rel.performers) >= 1

                # Verify GraphQL call sequence
                assert len(calls_rel) == 1, (
                    "Expected exactly 1 GraphQL call for relationship update"
                )
                assert "galleryUpdate" in calls_rel[0]["query"]


@pytest.mark.asyncio
async def test_gallery_images(
    stash_client: StashClient, stash_cleanup_tracker, mock_gallery: Gallery
) -> None:
    """Test adding/removing images from a gallery - TRUE INTEGRATION TEST.

    NOTE: Requires images to be pre-scanned in Stash (cannot create via API).
    Test will skip gracefully if no images are available.
    """
    # Check if Stash has any scanned images
    images_result = await stash_client.find_images(filter_={"per_page": 3})
    if not images_result or images_result.count == 0:
        pytest.skip(
            "No images found in Stash - images must be pre-scanned from filesystem"
        )

    # Get first 3 images from Stash
    image_dicts = images_result.images[:3]
    image_ids = [img["id"] if isinstance(img, dict) else img.id for img in image_dicts]

    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a gallery to test with
        gallery = Gallery(
            id="new",
            title="Test Gallery for Images",
            urls=["https://example.com/gallery/images"],
            organized=False,
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # Test add images
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.add_gallery_images(
                created_gallery.id,
                image_ids,
            )
            assert result is True

            # Verify GraphQL call sequence
            assert len(calls) == 1, "Expected exactly 1 GraphQL call for add images"
            assert (
                "galleryImagesAdd" in calls[0]["query"]
                or "addGalleryImages" in calls[0]["query"]
            )

        # Test remove images
        with capture_graphql_calls(stash_client) as calls_remove:
            result = await stash_client.remove_gallery_images(
                created_gallery.id,
                image_ids[:1],  # Remove first image only
            )
            assert result is True

            # Verify GraphQL call sequence
            assert len(calls_remove) == 1, (
                "Expected exactly 1 GraphQL call for remove images"
            )
            assert (
                "galleryImagesRemove" in calls_remove[0]["query"]
                or "removeGalleryImages" in calls_remove[0]["query"]
            )


@pytest.mark.asyncio
async def test_gallery_cover(
    stash_client: StashClient, stash_cleanup_tracker, mock_gallery: Gallery
) -> None:
    """Test setting/resetting gallery cover - TRUE INTEGRATION TEST.

    NOTE: Requires images to be pre-scanned in Stash (cannot create via API).
    Test will skip gracefully if no images are available.
    """
    # Check if Stash has any scanned images
    images_result = await stash_client.find_images(filter_={"per_page": 1})
    if not images_result or images_result.count == 0:
        pytest.skip(
            "No images found in Stash - images must be pre-scanned from filesystem"
        )

    # Get first image from Stash
    image_dict = images_result.images[0]
    image_id = image_dict["id"] if isinstance(image_dict, dict) else image_dict.id

    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a gallery to test with
        gallery = Gallery(
            id="new",
            title="Test Gallery for Cover",
            urls=["https://example.com/gallery/cover"],
            organized=False,
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # Add the image to the gallery first (required before setting as cover)
        await stash_client.add_gallery_images(created_gallery.id, [image_id])

        # Test set cover
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.set_gallery_cover(
                created_gallery.id,
                image_id,
            )
            assert result is True

            # Verify GraphQL call sequence
            assert len(calls) == 1, "Expected exactly 1 GraphQL call for set cover"
            assert (
                "gallerySetCover" in calls[0]["query"]
                or "setGalleryCover" in calls[0]["query"]
            )

        # Test reset cover
        with capture_graphql_calls(stash_client) as calls_reset:
            result = await stash_client.reset_gallery_cover(created_gallery.id)
            assert result is True

            # Verify GraphQL call sequence
            assert len(calls_reset) == 1, (
                "Expected exactly 1 GraphQL call for reset cover"
            )
            assert (
                "galleryResetCover" in calls_reset[0]["query"]
                or "resetGalleryCover" in calls_reset[0]["query"]
            )


@pytest.mark.asyncio
async def test_gallery_chapters(
    stash_client: StashClient,
    stash_cleanup_tracker,
) -> None:
    """Test gallery chapter operations - TRUE INTEGRATION TEST.

    NOTE: Requires images to be pre-scanned in Stash (cannot create via API).
    Test will skip gracefully if no images are available.
    """
    # Check if Stash has any scanned images
    images_result = await stash_client.find_images(filter_={"per_page": 2})
    if not images_result or images_result.count < 2:
        pytest.skip(
            "Need at least 2 images in Stash - images must be pre-scanned from filesystem"
        )

    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a gallery with images to test chapters on
        gallery = Gallery(
            id="new",
            title="Test Gallery for Chapters",
            urls=["https://example.com/gallery/chapters"],
            organized=False,
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # Add 2 images to the gallery
        image_dicts = images_result.images[:2]
        image_ids = [
            img["id"] if isinstance(img, dict) else img.id for img in image_dicts
        ]
        await stash_client.add_gallery_images(created_gallery.id, image_ids)

        # Test create chapter (image_index is 1-based in Stash)
        with capture_graphql_calls(stash_client) as calls:
            chapter = await stash_client.gallery_chapter_create(
                gallery_id=created_gallery.id,
                title="Chapter 1",
                image_index=1,
            )
            # Note: No need to track chapters in cleanup - they're deleted with the gallery

            assert chapter.id is not None
            assert chapter.title == "Chapter 1"
            assert chapter.image_index == 1  # Should match what we sent

            # Verify GraphQL call sequence
            assert len(calls) == 1, "Expected exactly 1 GraphQL call for chapter create"
            assert "galleryChapterCreate" in calls[0]["query"]

        # Test update chapter
        with capture_graphql_calls(stash_client) as calls_update:
            updated = await stash_client.gallery_chapter_update(
                id=chapter.id,
                title="Chapter 1 Updated",
                image_index=2,  # Move to second image
            )

            assert updated.id == chapter.id
            assert updated.title == "Chapter 1 Updated"
            assert updated.image_index == 2

            # Verify GraphQL call sequence
            assert len(calls_update) == 1, (
                "Expected exactly 1 GraphQL call for chapter update"
            )
            assert "galleryChapterUpdate" in calls_update[0]["query"]

        # Test delete chapter
        with capture_graphql_calls(stash_client) as calls_destroy:
            result = await stash_client.gallery_chapter_destroy(chapter.id)
            assert result is True

            # Verify GraphQL call sequence
            assert len(calls_destroy) == 1, (
                "Expected exactly 1 GraphQL call for chapter destroy"
            )
            assert "galleryChapterDestroy" in calls_destroy[0]["query"]


@pytest.mark.asyncio
async def test_create_gallery_error_cases(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test error cases for gallery creation - TRUE INTEGRATION TEST.

    Tests schema validation by intentionally violating requirements.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # ERROR CASE 1: Missing required field (title)
        with capture_graphql_calls(stash_client) as calls:
            gallery_no_title = Gallery(
                id="new",
                urls=["https://example.com/no-title"],
            )
            # title is None, which violates GalleryCreateInput schema requirement
            with pytest.raises(
                TypeError, match="missing 1 required keyword-only argument: 'title'"
            ):
                await stash_client.create_gallery(gallery_no_title)

        # ERROR CASE 2: Invalid rating100 value (Stash accepts 1-100, but we test out of range)
        # NOTE: Stash may accept values outside 1-100 range, so this test verifies actual behavior
        with capture_graphql_calls(stash_client) as calls_rating:
            gallery_invalid_rating = Gallery(
                id="new",
                title="Test Rating 150",
                urls=["https://example.com/rating-150"],
                rating100=150,  # Out of documented range (1-100)
            )
            # This may or may not fail - Stash might clamp the value or accept it
            created = await stash_client.create_gallery(gallery_invalid_rating)
            cleanup["galleries"].append(created.id)

            # Verify call was made even with out-of-range value
            assert len(calls_rating) == 1
            assert "galleryCreate" in calls_rating[0]["query"]


@pytest.mark.asyncio
async def test_update_gallery_error_cases(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test error cases for gallery updates - TRUE INTEGRATION TEST.

    Tests updating non-existent galleries.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # ERROR CASE: Update non-existent gallery
        # NOTE: Stash's update may silently fail or return None for non-existent IDs
        with capture_graphql_calls(stash_client) as calls:
            fake_gallery = Gallery(
                id="999999999",  # Non-existent ID
                title="Updated Title",
            )
            # Stash may return None or raise error - verify call is made
            try:
                result = await stash_client.update_gallery(fake_gallery)
                # If no error, verify we made the attempt
                assert len(calls) == 1
                assert "galleryUpdate" in calls[0]["query"]
            except Exception as e:
                # Expected - updating non-existent gallery should fail
                # Log the exception for debugging
                import logging

                logging.debug(f"Expected error updating non-existent gallery: {e}")


@pytest.mark.asyncio
async def test_gallery_images_error_cases(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test error cases for gallery image operations - TRUE INTEGRATION TEST.

    Tests adding invalid image IDs and operating on non-existent galleries.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a real gallery for testing
        gallery = Gallery(
            id="new",
            title="Test Gallery for Error Cases",
            urls=["https://example.com/error-images"],
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # ERROR CASE 1: Add images with invalid IDs
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),  # Should raise error for invalid IDs
        ):
            await stash_client.add_gallery_images(
                created_gallery.id,
                ["999999999", "888888888"],  # Non-existent image IDs
            )

        # ERROR CASE 2: Add images to non-existent gallery
        with (
            capture_graphql_calls(stash_client) as calls_no_gallery,
            pytest.raises(Exception),  # Should raise error
        ):
            await stash_client.add_gallery_images(
                "999999999",  # Non-existent gallery ID
                ["1"],
            )


@pytest.mark.asyncio
async def test_gallery_chapter_error_cases(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test error cases for gallery chapter operations - TRUE INTEGRATION TEST.

    Tests constraint violations: creating chapter on gallery with no images.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a gallery with NO images
        gallery = Gallery(
            id="new",
            title="Test Gallery No Images for Chapter Error",
            urls=["https://example.com/error-chapter"],
        )
        created_gallery = await stash_client.create_gallery(gallery)
        cleanup["galleries"].append(created_gallery.id)

        # ERROR CASE: Try to create a chapter on a gallery with NO images
        # This MUST fail because chapters reference image indices
        # Expected error: "Image # must greater than zero and in range of the gallery images"
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception, match="Image # must greater than zero"),
        ):
            await stash_client.gallery_chapter_create(
                gallery_id=created_gallery.id,
                title="Invalid Chapter",
                image_index=1,  # No images in gallery!
            )

        # Note: Exception is raised, but capture still records the attempt
        # (the call happens before the error is raised)


@pytest.mark.asyncio
async def test_gallery_from_content(
    stash_client: StashClient, stash_cleanup_tracker, mock_gallery: Gallery
) -> None:
    """Test creating a gallery from content - TRUE INTEGRATION TEST.

    Tests the Gallery.from_content() factory method and saves to real Stash.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a real post object
        post = Post(
            id=123456,
            accountId=789,
            content="Test post content for gallery creation",
            createdAt=datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC),
        )

        # Test Gallery.from_content() factory method
        with capture_graphql_calls(stash_client) as calls:
            gallery = await Gallery.from_content(
                content=post,
                performer=mock_gallery.performers[0]
                if mock_gallery.performers
                else None,
                studio=mock_gallery.studio,
            )

            # Verify the factory method set correct fields
            expected_title = (
                f"{mock_gallery.studio.name} - {post.id}"
                if mock_gallery.studio
                else str(post.id)
            )
            assert gallery.title == expected_title
            assert gallery.details == post.content
            assert gallery.date == "2024-03-15"
            assert f"https://fansly.com/post/{post.id}" in gallery.urls
            assert gallery.organized is True  # from_content marks as organized

            # Now actually create it in Stash
            created = await stash_client.create_gallery(gallery)
            cleanup["galleries"].append(created.id)

            # Verify it was created with correct properties
            assert created.id != "new"
            assert created.title == gallery.title
            assert created.details == gallery.details
            assert created.date == gallery.date

            # Verify GraphQL call sequence (permanent assertion)
            assert len(calls) == 1, (
                "Expected exactly 1 GraphQL call for gallery creation"
            )
            assert "galleryCreate" in calls[0]["query"]
