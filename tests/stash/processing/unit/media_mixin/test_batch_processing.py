"""Tests for media batch processing methods.

This module tests the batch processing methods that handle collections of media
objects efficiently by grouping them by mimetype and processing in batches.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

from unittest.mock import patch

import pytest

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash.stash_type_factories import (
    ImageFactory,
    ImageFileFactory,
    SceneFactory,
    VideoFileFactory,
)


class TestBatchProcessing:
    """Test media batch processing methods."""

    @pytest.mark.asyncio
    async def test_process_media_batch_small(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_media_batch_by_mimetype with small batch (< 20 items)."""
        # Create a small batch of media (under max_batch_size of 20)
        media_list = []
        for i in range(5):
            media = MediaFactory.build(
                id=20000 + i,
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=f"stash_{20000 + i}",
            )
            media_list.append(media)

        # Track calls to the internal processing method
        internal_calls = []

        async def mock_process_internal(media_list, item, account):
            internal_calls.append(
                {
                    "media_count": len(media_list),
                    "media_ids": [m.id for m in media_list],
                }
            )
            # Return fake results
            return {"images": [ImageFactory() for _ in media_list], "scenes": []}

        # Mock the internal batch processing method
        with patch.object(
            respx_stash_processor, "_process_batch_internal", mock_process_internal
        ):
            # Call the method
            result = await respx_stash_processor._process_media_batch_by_mimetype(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

            # Verify it called _process_batch_internal ONCE (no splitting)
            assert len(internal_calls) == 1
            assert internal_calls[0]["media_count"] == 5

            # Verify results
            assert len(result["images"]) == 5
            assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_media_batch_large(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_media_batch_by_mimetype splits large batches (> 20 items)."""
        # Create a large batch that exceeds max_batch_size of 20
        media_list = []
        for i in range(45):  # 45 items should split into 3 batches (20+20+5)
            media = MediaFactory.build(
                id=30000 + i,
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=f"stash_{30000 + i}",
            )
            media_list.append(media)

        # Track calls to the internal processing method
        internal_calls = []

        async def mock_process_internal(media_list, item, account):
            internal_calls.append(
                {
                    "media_count": len(media_list),
                }
            )
            # Return fake results
            return {"images": [ImageFactory() for _ in media_list], "scenes": []}

        # Mock the internal batch processing method
        with patch.object(
            respx_stash_processor, "_process_batch_internal", mock_process_internal
        ):
            # Call the method
            result = await respx_stash_processor._process_media_batch_by_mimetype(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

            # Verify it split into multiple batches (3 batches: 20, 20, 5)
            assert len(internal_calls) == 3
            assert internal_calls[0]["media_count"] == 20
            assert internal_calls[1]["media_count"] == 20
            assert internal_calls[2]["media_count"] == 5

            # Verify all results were collected
            assert len(result["images"]) == 45
            assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_stash_ids(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_batch_internal processes media with stash_ids."""
        # Create media with stash_ids
        media_list = []
        for i in range(3):
            media = MediaFactory.build(
                id=40000 + i,
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                stash_id=f"stash_id_{40000 + i}",
            )
            media_list.append(media)

        # Mock the stash file lookup methods
        find_by_id_calls = []

        async def mock_find_by_id(lookup_data):
            find_by_id_calls.append({"lookup_count": len(lookup_data)})
            # Return fake results
            results = []
            for stash_id, _mimetype in lookup_data:
                image = ImageFactory(id=stash_id)
                image_file = ImageFileFactory()
                results.append((image, image_file))
            return results

        update_calls = []

        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            update_calls.append(
                {
                    "stash_obj_id": stash_obj.id,
                    "media_id": media_id,
                }
            )

        # Mock the methods using patch.object
        with (
            patch.object(
                respx_stash_processor, "_find_stash_files_by_id", mock_find_by_id
            ),
            patch.object(
                respx_stash_processor, "_update_stash_metadata", mock_update_metadata
            ),
        ):
            # Call the method
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

            # Verify it called _find_stash_files_by_id
            assert len(find_by_id_calls) == 1
            assert find_by_id_calls[0]["lookup_count"] == 3

            # Verify metadata was updated for each media
            assert len(update_calls) == 3

            # Verify results
            assert len(result["images"]) == 3
            assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_with_paths(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_batch_internal processes media without stash_ids (path-based)."""
        # Create media WITHOUT stash_ids (will use path-based lookup)
        media_list = []
        for i in range(3):
            media = MediaFactory.build(
                id=50000 + i,
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
                # NO stash_id - will trigger path-based lookup
            )
            media.variants = set()  # No variants
            media_list.append(media)

        # Mock the path-based lookup methods
        find_by_path_calls = []

        async def mock_find_by_path(lookup_data):
            find_by_path_calls.append({"lookup_count": len(lookup_data)})
            # Return fake results
            results = []
            for path, _mimetype in lookup_data:
                image = ImageFactory()
                image_file = ImageFileFactory(path=f"/stash/media/{path}.jpg")
                results.append((image, image_file))
            return results

        update_calls = []

        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            update_calls.append({"media_id": media_id})

        # Mock the methods using patch.object
        with (
            patch.object(
                respx_stash_processor, "_find_stash_files_by_path", mock_find_by_path
            ),
            patch.object(
                respx_stash_processor, "_update_stash_metadata", mock_update_metadata
            ),
        ):
            # Call the method
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

            # Verify it called _find_stash_files_by_path (for images)
            assert len(find_by_path_calls) == 1
            assert find_by_path_calls[0]["lookup_count"] == 3

            # Verify metadata was updated
            assert len(update_calls) == 3

            # Verify results
            assert len(result["images"]) == 3
            assert len(result["scenes"]) == 0

    @pytest.mark.asyncio
    async def test_process_batch_internal_mixed_mimetype(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_batch_internal with mixed mimetypes (images + videos)."""
        # Create mixed media (images and videos)
        media_list = []

        # Add 2 images
        for i in range(2):
            media = MediaFactory.build(
                id=60000 + i,
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=mock_account.id,
            )
            media.variants = set()
            media_list.append(media)

        # Add 2 videos
        for i in range(2):
            media = MediaFactory.build(
                id=60010 + i,
                mimetype="video/mp4",
                is_downloaded=True,
                accountId=mock_account.id,
            )
            media.variants = set()
            media_list.append(media)

        # Mock path-based lookup to return appropriate types
        async def mock_find_by_path(lookup_data):
            results = []
            for path, mimetype in lookup_data:
                if mimetype.startswith("image"):
                    stash_obj = ImageFactory()
                    file_obj = ImageFileFactory(path=f"/stash/media/{path}.jpg")
                else:
                    stash_obj = SceneFactory()
                    file_obj = VideoFileFactory(path=f"/stash/media/{path}.mp4")
                results.append((stash_obj, file_obj))
            return results

        async def mock_update_metadata(*args, **kwargs):
            """No-op async mock for metadata update."""

        # Mock the methods using patch.object
        with (
            patch.object(
                respx_stash_processor, "_find_stash_files_by_path", mock_find_by_path
            ),
            patch.object(
                respx_stash_processor, "_update_stash_metadata", mock_update_metadata
            ),
        ):
            # Call the method
            result = await respx_stash_processor._process_batch_internal(
                media_list=media_list,
                item=mock_item,
                account=mock_account,
            )

            # Verify results contain both images and scenes
            assert len(result["images"]) == 2
            assert len(result["scenes"]) == 2

    @pytest.mark.asyncio
    async def test_process_batch_internal_empty_list(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test _process_batch_internal handles empty media list gracefully."""
        # Call with empty list
        result = await respx_stash_processor._process_batch_internal(
            media_list=[],
            item=mock_item,
            account=mock_account,
        )

        # Verify empty results
        assert result["images"] == []
        assert result["scenes"] == []
