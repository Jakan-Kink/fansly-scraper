"""Tests for media processing methods in MediaProcessingMixin."""

import pytest

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash.stash_type_factories import ImageFactory, ImageFileFactory


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    # NOTE: This helper method is deprecated and should be removed once all tests
    # are refactored to use factory-based approaches instead of AccessibleAsyncMock
    # @staticmethod
    # def _convert_to_accessible_mock(mock_obj) -> AccessibleAsyncMock | None:
    #     """Convert a regular mock to an AccessibleAsyncMock with proper attributes."""
    #     if mock_obj is None:
    #         return None
    #
    #     accessible_mock = AccessibleAsyncMock()
    #
    #     # Copy all non-private attributes
    #     for key, value in mock_obj.__dict__.items():
    #         if not key.startswith("_"):
    #             setattr(accessible_mock, key, value)
    #
    #     # Ensure it's properly awaitable
    #     accessible_mock.__await__ = lambda: async_return(accessible_mock)().__await__()
    #     return accessible_mock

    @pytest.mark.asyncio
    async def test_process_media(
        self, media_mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method."""
        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create real Media object using factory WITH stash_id
        media = MediaFactory.build(
            id=mock_media.id,
            mimetype=mock_media.mimetype,
            is_downloaded=mock_media.is_downloaded,
            accountId=mock_account.id,
            stash_id="stash_456",  # Add stash_id so _find_stash_files_by_id gets called
        )
        media.variants = set()
        # Note: SQLAlchemy objects already have awaitable_attrs, no need to add it

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
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
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_id = original_find
            media_mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_stash_id(
        self, media_mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method with stash_id."""
        # Create real Media object using factory with stash_id
        media = MediaFactory.build(
            id=mock_media.id,
            mimetype=mock_media.mimetype,
            is_downloaded=mock_media.is_downloaded,
            accountId=mock_account.id,
        )
        media.stash_id = "stash_123"
        media.variants = set()
        # Note: SQLAlchemy objects already have awaitable_attrs, no need to add it

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
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
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_id = original_find
            media_mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_variants(
        self, media_mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method with variants."""
        # Create real variant Media objects using factory
        variant1 = MediaFactory.build(
            id="variant_1",
            mimetype="image/jpeg",
            accountId=mock_account.id,
        )
        variant2 = MediaFactory.build(
            id="variant_2",
            mimetype="video/mp4",
            accountId=mock_account.id,
        )
        variants = {variant1, variant2}  # Use set, not list

        # Create real Media object using factory with variants
        media = MediaFactory.build(
            id=mock_media.id,
            mimetype=mock_media.mimetype,
            is_downloaded=mock_media.is_downloaded,
            accountId=mock_account.id,
        )
        media.variants = variants
        # Note: SQLAlchemy objects already have awaitable_attrs, no need to add it

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_path(media_files):
            # Return fake results using factories
            image = ImageFactory()
            image_file = ImageFileFactory()
            return [(image, image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
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
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(media.id)
        finally:
            # Restore original methods
            media_mixin._find_stash_files_by_path = original_find
            media_mixin._update_stash_metadata = original_update
