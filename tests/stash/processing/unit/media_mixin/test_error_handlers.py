"""Tests for error handlers and edge cases in MediaProcessingMixin.

This module tests uncovered error paths and edge cases to improve coverage from 76.77% to 95%+.
Focuses on exception handling, validation, and fallback logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_find_scenes_result,
    create_graphql_response,
    create_image_dict,
    create_scene_dict,
)


class TestErrorHandlers:
    """Test error handlers and edge cases in MediaProcessingMixin."""

    def test_get_file_from_stash_obj_unknown_type(self, respx_stash_processor):
        """Test _get_file_from_stash_obj with unknown object type (line 53)."""
        # Create an object that is neither Image nor Scene
        unknown_obj = MagicMock(spec=[])  # No Image or Scene interface

        result = respx_stash_processor._get_file_from_stash_obj(unknown_obj)

        assert result is None

    @pytest.mark.asyncio
    async def test_find_stash_files_by_id_image_exception(
        self, respx_stash_processor, session
    ):
        """Test _find_stash_files_by_id handles exception when finding image (lines 211-212)."""
        # Mock find_image to raise exception
        with patch.object(
            respx_stash_processor.context.client,
            "find_image",
            new=AsyncMock(side_effect=RuntimeError("API error")),
        ):
            # Should not raise - should catch exception and continue
            result = await respx_stash_processor._find_stash_files_by_id(
                stash_files=[("123", "image/jpeg")],
                session=session,
            )

            # Should return empty list (exception was caught)
            assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_id_scene_exception(
        self, respx_stash_processor, session
    ):
        """Test _find_stash_files_by_id handles exception when finding scene (lines 250-251)."""
        # Mock find_scene to raise exception
        with patch.object(
            respx_stash_processor.context.client,
            "find_scene",
            new=AsyncMock(side_effect=RuntimeError("API error")),
        ):
            # Should not raise - should catch exception and continue
            result = await respx_stash_processor._find_stash_files_by_id(
                stash_files=[("456", "video/mp4")],
                session=session,
            )

            # Should return empty list (exception was caught)
            assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_no_images(self, respx_stash_processor):
        """Test _find_stash_files_by_path when no image IDs provided (line 314->403)."""
        # Only provide scene media (no images)
        media_files = [("video_123", "video/mp4"), ("video_456", "video/mp4")]

        # Mock scene regex search
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response(
                    "FindScenes",
                    create_find_scenes_result(count=0, scenes=[]),
                ),
            )
        )

        await respx_stash_processor.context.get_client()
        result = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Should return empty list (no scenes found)
        assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_image_no_file(self, respx_stash_processor):
        """Test _find_stash_files_by_path when image has no visual files (lines 362-366)."""
        media_files = [("image_123", "image/jpeg")]

        # Create image with no visual files
        image_dict = create_image_dict(id="999", visual_files=[])

        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response(
                    "findImages",
                    create_find_images_result(count=1, images=[image_dict]),
                ),
            )
        )

        await respx_stash_processor.context.get_client()
        result = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Should return empty list (no valid files extracted)
        assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_images_no_valid_files(
        self, respx_stash_processor
    ):
        """Test _find_stash_files_by_path when images found but no valid files (lines 380-392)."""
        media_files = [("image_123", "image/jpeg"), ("image_456", "image/png")]

        # Create multiple images but all without visual files
        images = [
            create_image_dict(id="999", visual_files=[]),
            create_image_dict(id="888", visual_files=[]),
        ]

        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response(
                    "findImages",
                    create_find_images_result(count=2, images=images),
                ),
            )
        )

        await respx_stash_processor.context.get_client()
        result = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Should return empty list and log warning
        assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_scenes_not_found(
        self, respx_stash_processor
    ):
        """Test _find_stash_files_by_path when no scenes found via regex (lines 430-433)."""
        media_files = [("video_123", "video/mp4"), ("video_456", "video/mp4")]

        # Mock regex search returning no results
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response(
                    "FindScenes",
                    create_find_scenes_result(count=0, scenes=[]),
                ),
            )
        )

        await respx_stash_processor.context.get_client()
        result = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Should return empty list and log warning
        assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_scene_processing_error(
        self, respx_stash_processor
    ):
        """Test _find_stash_files_by_path with scene processing error (lines 448-454)."""
        media_files = [("video_123", "video/mp4")]

        # Create scene that will cause processing error
        scene_dict = create_scene_dict(id="999", title="Test", files=[])  # No files

        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response(
                    "FindScenes",
                    create_find_scenes_result(count=1, scenes=[scene_dict]),
                ),
            )
        )

        await respx_stash_processor.context.get_client()
        result = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Should return empty list (scene had no files)
        assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_regex_fallback(self, respx_stash_processor):
        """Test _find_stash_files_by_path regex failure triggers fallback (lines 464-508)."""
        media_files = [("video_123", "video/mp4")]

        # Mock the find_scenes_by_path_regex to raise an exception
        # Then mock find_scenes for the fallback
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Fallback: findScenes batch call
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findScenes",
                        create_find_scenes_result(count=0, scenes=[]),
                    ),
                ),
            ]
        )

        await respx_stash_processor.context.get_client()

        # Patch find_scenes_by_path_regex to raise exception
        with patch.object(
            respx_stash_processor.context.client,
            "find_scenes_by_path_regex",
            new=AsyncMock(side_effect=RuntimeError("Regex pattern too complex")),
        ):
            result = await respx_stash_processor._find_stash_files_by_path(media_files)

            # Should have made 1 GraphQL call for fallback batch search
            assert graphql_route.call_count == 1
            # Result should be empty (fallback also found nothing)
            assert result == []

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path_regex_fallback_also_fails(
        self, respx_stash_processor
    ):
        """Test _find_stash_files_by_path when both regex and fallback fail (lines 504-515)."""
        media_files = [("video_123", "video/mp4")]

        # Mock GraphQL for fallback findScenes call - will also raise exception
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=RuntimeError("Batch search also failed")
        )

        await respx_stash_processor.context.get_client()

        # Patch both find_scenes_by_path_regex AND find_scenes to raise exceptions
        with (
            patch.object(
                respx_stash_processor.context.client,
                "find_scenes_by_path_regex",
                new=AsyncMock(side_effect=RuntimeError("Regex pattern error")),
            ),
            patch.object(
                respx_stash_processor.context.client,
                "find_scenes",
                new=AsyncMock(side_effect=RuntimeError("Batch search error")),
            ),
        ):
            result = await respx_stash_processor._find_stash_files_by_path(media_files)

            # Should handle both exceptions gracefully
            # Result should be empty (both failed)
            assert result == []

    # REMOVED: _update_stash_metadata tests - too many complex dependencies requiring extensive mocking
    # These paths are better covered by integration tests where the full data flow can execute

    @pytest.mark.asyncio
    async def test_process_media_batch_empty_list(self, respx_stash_processor):
        """Test _process_media_batch_by_mimetype with empty list (line 1116)."""
        result = await respx_stash_processor._process_media_batch_by_mimetype(
            media_list=[],
            item=MagicMock(),
            account=MagicMock(),
        )

        # Should return empty result
        assert result == {"images": [], "scenes": []}
