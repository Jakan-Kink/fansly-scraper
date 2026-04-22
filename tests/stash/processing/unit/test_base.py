"""Unit tests for StashProcessingBase class - pure logic methods."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_graphql_response,
    create_performer_dict,
    create_tag_dict,
)
from tests.fixtures.stash.stash_type_factories import (
    ImageFactory,
    ImageFileFactory,
    SceneFactory,
    VideoFileFactory,
)


class TestStashProcessingBase:
    """Test pure logic methods of StashProcessingBase class."""

    def test_generate_title_from_content(self, respx_stash_processor, faker):
        """Test _generate_title_from_content method - pure logic test."""
        # Test case 1: Content with a short first line
        content = "This is the title\nThis is the rest of the content"
        username = faker.user_name()
        created_at = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        # Call method on real processor (inherits from StashProcessingBase)
        title = respx_stash_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result
        assert title == "This is the title"

        # Test case 2: Content with a very long first line
        long_content = "X" * 200
        title = respx_stash_processor._generate_title_from_content(
            long_content, username, created_at
        )

        # Verify result is truncated with ellipsis
        assert title == ("X" * 125 + "...")
        assert len(title) == 128

        # Test case 3: No suitable content line, use fallback
        title = respx_stash_processor._generate_title_from_content(
            None, username, created_at
        )

        # Verify fallback format
        assert title == f"{username} - 2023/01/01"

        # Test case 4: With position indicators
        title = respx_stash_processor._generate_title_from_content(
            content, username, created_at, 2, 5
        )

        # Verify position is appended
        assert title == "This is the title - 2/5"


class TestSceneIndexing:
    """Test _index_scene_files and find_scenes_by_media_codes (lines 222-265)."""

    def test_index_scene_with_id_marker(self, respx_stash_processor):
        """Scene files with _id_ marker are indexed by media code (lines 230-235)."""
        video = VideoFileFactory(
            path="/downloads/creator/2024_01_15_at_12_00_00_UTC_id_987654.mp4"
        )
        scene = SceneFactory(id="100", files=[video])

        respx_stash_processor._index_scene_files(scene)

        assert "987654" in respx_stash_processor._scene_code_index
        assert respx_stash_processor._scene_code_index["987654"] == [scene]

    def test_index_scene_with_preview_id_marker(self, respx_stash_processor):
        """Scene files with _preview_id_ marker are indexed (line 230)."""
        video = VideoFileFactory(
            path="/downloads/creator/2024_01_15_at_12_00_00_UTC_preview_id_111222.mp4"
        )
        scene = SceneFactory(id="101", files=[video])

        respx_stash_processor._index_scene_files(scene)

        assert "111222" in respx_stash_processor._scene_code_index

    def test_index_scene_no_files_skips(self, respx_stash_processor):
        """Scene without files is skipped (line 223-224)."""
        scene = SceneFactory(id="102")
        # files is UNSET by default from factory

        respx_stash_processor._index_scene_files(scene)

        assert len(respx_stash_processor._scene_code_index) == 0

    def test_find_scenes_deduplicates(self, respx_stash_processor):
        """find_scenes_by_media_codes deduplicates by ID (lines 258-264)."""
        video1 = VideoFileFactory(
            path="/downloads/creator/2024_01_15_at_12_00_00_UTC_id_555.mp4"
        )
        scene = SceneFactory(id="200", files=[video1])

        # Index the same scene twice (e.g., matched by both _id_ and _preview_id_)
        respx_stash_processor._scene_code_index["555"].append(scene)
        respx_stash_processor._scene_code_index["555"].append(scene)

        result = respx_stash_processor.find_scenes_by_media_codes(["555"])

        assert len(result["555"]) == 1  # Deduplicated by ID

    def test_find_scenes_no_match(self, respx_stash_processor):
        """find_scenes_by_media_codes returns empty for unmatched codes (line 257)."""
        result = respx_stash_processor.find_scenes_by_media_codes(["nonexistent"])
        assert result == {}


class TestImageIndexing:
    """Test _index_image_files and find_images_by_media_codes (lines 237-282)."""

    def test_index_image_with_id_marker(self, respx_stash_processor):
        """Image visual files with _id_ marker are indexed (lines 242-248)."""
        img_file = ImageFileFactory(
            path="/downloads/creator/2024_01_15_at_12_00_00_UTC_id_333444.jpg"
        )
        image = ImageFactory(id="300", visual_files=[img_file])

        respx_stash_processor._index_image_files(image)

        assert "333444" in respx_stash_processor._image_code_index
        assert respx_stash_processor._image_code_index["333444"] == [image]

    def test_index_image_no_visual_files_skips(self, respx_stash_processor):
        """Image without visual_files is skipped (line 239-240)."""
        image = ImageFactory(id="301")
        # visual_files is UNSET by default from factory

        respx_stash_processor._index_image_files(image)

        assert len(respx_stash_processor._image_code_index) == 0

    def test_find_images_deduplicates(self, respx_stash_processor):
        """find_images_by_media_codes deduplicates by ID (lines 275-281)."""
        img_file = ImageFileFactory(
            path="/downloads/creator/2024_01_15_at_12_00_00_UTC_id_666.jpg"
        )
        image = ImageFactory(id="400", visual_files=[img_file])

        respx_stash_processor._image_code_index["666"].append(image)
        respx_stash_processor._image_code_index["666"].append(image)

        result = respx_stash_processor.find_images_by_media_codes(["666"])

        assert len(result["666"]) == 1

    def test_find_images_no_match(self, respx_stash_processor):
        """find_images_by_media_codes returns empty for unmatched codes."""
        result = respx_stash_processor.find_images_by_media_codes(["nope"])
        assert result == {}


class TestPreloadCreatorMedia:
    """Test _preload_creator_media edge cases (lines 163-219)."""

    @pytest.mark.asyncio
    async def test_no_base_path_skips(self, respx_stash_processor):
        """No base_path set → early return with debug log (lines 173-175)."""
        respx_stash_processor.state.base_path = None

        await respx_stash_processor._preload_creator_media()

        # Should be a no-op — index should be empty
        assert len(respx_stash_processor._scene_code_index) == 0
        assert len(respx_stash_processor._image_code_index) == 0


class TestPreloadEntities:
    """Test _preload_stash_entities and _preload_creator_media."""

    @pytest.mark.asyncio
    async def test_preload_entities_with_data(self, respx_stash_processor):
        """_preload_stash_entities iterates yielded entities (line 146)."""
        performer = create_performer_dict(id="p1", name="Performer One")
        tag = create_tag_dict(id="t1", name="Tag One")
        studio_dict = {"id": "s1", "name": "Studio One", "urls": []}

        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 1, "performers": [performer]}
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findTags", {"count": 1, "tags": [tag]}
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", {"count": 1, "studios": [studio_dict]}
                    ),
                ),
            ]
        )

        try:
            await respx_stash_processor._preload_stash_entities()
        finally:
            dump_graphql_calls(route.calls, "test_preload_entities_with_data")

        assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_preload_entities_exception(self, respx_stash_processor):
        """_preload_stash_entities catches exceptions (lines 153-154)."""
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "server error"}],
                        "data": None,
                    },
                ),
            ]
        )

        try:
            await respx_stash_processor._preload_stash_entities()
        finally:
            dump_graphql_calls(route.calls, "test_preload_entities_exception")

    @pytest.mark.asyncio
    async def test_preload_no_base_path(self, respx_stash_processor):
        """No base_path set → early return from _preload_creator_media."""
        respx_stash_processor.state.base_path = None

        await respx_stash_processor._preload_creator_media()
        assert len(respx_stash_processor._scene_code_index) == 0
