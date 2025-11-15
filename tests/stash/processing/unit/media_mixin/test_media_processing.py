"""Tests for media processing methods in MediaProcessingMixin."""

import pytest

from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_type_factories import ImageFactory, ImageFileFactory


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_media(self, media_mixin):
        """Test _process_media method."""
        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create test data directly using factories
        account = AccountFactory.build(id=123)
        item = PostFactory.build(id=456, accountId=123)

        # Create real Media object using factory WITH stash_id
        media = MediaFactory.build(
            id=789,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
            stash_id="stash_456",  # Add stash_id so _find_stash_files_by_id gets called
        )
        media.variants = set()

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(stash_obj, **kwargs):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": kwargs.get("item"),
                    "account": kwargs.get("account"),
                    "media_id": kwargs.get("media_id"),
                    "is_preview": kwargs.get("is_preview", False),
                }
            )

        # Mock the relevant methods
        original_find = media_mixin._find_stash_files_by_id
        original_update = media_mixin._update_stash_metadata
        media_mixin._find_stash_files_by_id = mock_find_by_stash_id
        media_mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await media_mixin._process_media(
                media=media,
                item=item,
                account=account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == item
            assert found_results[0]["account"] == account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_id = original_find
            media_mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_stash_id(self, media_mixin):
        """Test _process_media method with stash_id."""
        # Create test data directly using factories
        account = AccountFactory.build(id=123)
        item = PostFactory.build(id=456, accountId=123)

        # Create real Media object using factory with stash_id
        media = MediaFactory.build(
            id=789,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
        )
        media.stash_id = "stash_123"
        media.variants = set()

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(stash_obj, **kwargs):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": kwargs.get("item"),
                    "account": kwargs.get("account"),
                    "media_id": kwargs.get("media_id"),
                    "is_preview": kwargs.get("is_preview", False),
                }
            )

        # Mock the relevant methods
        original_find = media_mixin._find_stash_files_by_id
        original_update = media_mixin._update_stash_metadata
        media_mixin._find_stash_files_by_id = mock_find_by_stash_id
        media_mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await media_mixin._process_media(
                media=media,
                item=item,
                account=account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == item
            assert found_results[0]["account"] == account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_id = original_find
            media_mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_variants(self, media_mixin):
        """Test _process_media method with variants."""
        # Create test data directly using factories
        account = AccountFactory.build(id=123)
        item = PostFactory.build(id=456, accountId=123)

        # Create real variant Media objects using factory
        variant1 = MediaFactory.build(
            id="variant_1",
            mimetype="image/jpeg",
            accountId=account.id,
        )
        variant2 = MediaFactory.build(
            id="variant_2",
            mimetype="video/mp4",
            accountId=account.id,
        )
        variants = {variant1, variant2}  # Use set, not list

        # Create real Media object using factory with variants
        media = MediaFactory.build(
            id=789,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=account.id,
        )
        media.variants = variants

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_path(media_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(stash_obj, **kwargs):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": kwargs.get("item"),
                    "account": kwargs.get("account"),
                    "media_id": kwargs.get("media_id"),
                    "is_preview": kwargs.get("is_preview", False),
                }
            )

        # Mock the relevant methods
        original_find = media_mixin._find_stash_files_by_path
        original_update = media_mixin._update_stash_metadata
        media_mixin._find_stash_files_by_path = mock_find_by_path
        media_mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await media_mixin._process_media(
                media=media,
                item=item,
                account=account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == item
            assert found_results[0]["account"] == account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_path = original_find
            media_mixin._update_stash_metadata = original_update
