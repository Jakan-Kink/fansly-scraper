"""Fixed tests for file handling methods in MediaProcessingMixin."""

import httpx
import pytest
import respx
from stash_graphql_client.types import ImageFile, VideoFile

from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_graphql_response,
    create_image_dict,
    create_scene_dict,
)
from tests.fixtures.stash.stash_type_factories import VideoFileFactory


class TestFileHandling:
    """Test file handling methods in MediaProcessingMixin."""

    def test_get_file_from_stash_obj_image(
        self, respx_stash_processor, mock_image, mock_image_file
    ):
        """Test _get_file_from_stash_obj method with Image object.

        Unit test - tests utility method that extracts ImageFile from Image object.
        """
        # Test with no visual files
        mock_image.visual_files = []

        file = respx_stash_processor._get_file_from_stash_obj(mock_image)

        # Verify no file returned
        assert file is None

        # Test with visual files as objects
        mock_image.visual_files = [mock_image_file]

        file = respx_stash_processor._get_file_from_stash_obj(mock_image)

        # Verify file returned
        assert file == mock_image_file

        # Test with visual files as dictionaries (with required fields)
        mock_image.visual_files = [
            {
                "id": "file_123",
                "path": "/path/to/image.jpg",
                "size": 12345,
                "width": 1920,
                "height": 1080,
                "basename": "image.jpg",
                "parent_folder_id": "folder_123",
                "fingerprints": [],
                "mod_time": "2024-01-01T00:00:00Z",
            }
        ]

        file = respx_stash_processor._get_file_from_stash_obj(mock_image)

        # Verify file returned and basic properties
        assert file is not None
        assert file.id == "file_123"
        assert file.path == "/path/to/image.jpg"
        assert file.size == 12345

        # Test with GIF file (VideoFile structure in Image)
        # This simulates what Stash returns for animated GIFs
        mock_image.visual_files = [
            {
                "id": "file_gif_123",
                "path": "/path/to/animation.gif",
                "basename": "animation.gif",
                "parent_folder_id": "folder_123",
                "size": 12099861,
                "mod_time": "2022-10-11T16:27:32Z",
                "fingerprints": [{"type": "md5", "value": "abcdef123456"}],
                # VideoFile specific fields
                "format": "gif",
                "width": 272,
                "height": 480,
                "duration": 19.18,
                "video_codec": "gif",
                "audio_codec": "",
                "frame_rate": 11.11,
                "bit_rate": 5046865,
            }
        ]

        file = respx_stash_processor._get_file_from_stash_obj(mock_image)

        # Verify VideoFile returned
        assert file is not None, "Failed to extract GIF file from Image object"
        assert isinstance(file, VideoFile), "GIF should be extracted as VideoFile"
        assert file.format == "gif"
        assert file.duration == 19.18

    def test_get_file_from_stash_obj_scene(
        self, respx_stash_processor, mock_scene, mock_video_file
    ):
        """Test _get_file_from_stash_obj method with Scene object.

        Unit test - tests utility method that extracts VideoFile from Scene object.
        """
        # Test with no files
        mock_scene.files = []

        file = respx_stash_processor._get_file_from_stash_obj(mock_scene)

        # Verify no file returned
        assert file is None

        # Test with files
        mock_scene.files = [mock_video_file]

        file = respx_stash_processor._get_file_from_stash_obj(mock_scene)

        # Verify first file returned
        assert file == mock_video_file

        # Test with multiple files (should return first)
        # Use VideoFileFactory instead of MagicMock
        mock_video_file2 = VideoFileFactory.build(id="file_789")
        mock_scene.files = [mock_video_file, mock_video_file2]

        file = respx_stash_processor._get_file_from_stash_obj(mock_scene)

        # Verify first file returned
        assert file == mock_video_file

    def test_create_targeted_regex_pattern(self, respx_stash_processor):
        """Test _create_targeted_regex_pattern method.

        After ORM migration: Tests utility method that builds regex patterns
        for Django-style path__regex filtering (replaces nested OR conditions).
        """
        # Test with single ID
        media_ids = ["123456"]
        result = respx_stash_processor._create_targeted_regex_pattern(media_ids)

        # Should return regex pattern string
        assert isinstance(result, str)
        assert "123456" in result

        # Test with multiple IDs - should be OR'ed in regex
        media_ids = ["123456", "789012"]
        result = respx_stash_processor._create_targeted_regex_pattern(media_ids)

        # Should contain both IDs in regex pattern
        assert isinstance(result, str)
        assert "123456" in result
        assert "789012" in result
        # Regex OR pattern uses pipe
        assert "|" in result

    @pytest.mark.asyncio
    async def test_find_stash_files_by_id(self, respx_stash_processor):
        """Test _find_stash_files_by_id method.

        Unit test using respx - tests stash ID lookup for both images and scenes.
        """
        # Expected GraphQL call sequence (will verify with debug):
        # 1: findImage for image_123
        # 2: findScene for scene_123

        # Response 1: findImage - return an image with visual_files
        image_file = {
            "__typename": "ImageFile",
            "id": "file_image_123",
            "path": "/path/to/image.jpg",
            "basename": "image.jpg",
            "size": 1024,
            "width": 1920,
            "height": 1080,
            "format": "jpg",
            "parent_folder_id": "folder_123",
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        image_result = create_image_dict(
            id="image_123",
            title="Test Image",
            visual_files=[image_file],
        )

        # Response 2: findScene - return a scene with files
        video_file = {
            "__typename": "VideoFile",
            "id": "file_scene_123",
            "path": "/path/to/video.mp4",
            "basename": "video.mp4",
            "size": 2048,
            "parent_folder_id": "folder_123",
            "format": "mp4",
            "width": 1920,
            "height": 1080,
            "duration": 120.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "frame_rate": 30.0,
            "bit_rate": 5000000,
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        scene_result = create_scene_dict(
            id="scene_123",
            title="Test Scene",
            files=[video_file],
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_result)
                ),
            ]
        )

        # Test with mix of image and scene stash IDs
        stash_files = [
            ("image_123", "image/jpeg"),
            ("scene_123", "video/mp4"),
        ]

        # Call method
        results = await respx_stash_processor._find_stash_files_by_id(stash_files)

        # Verify results structure
        assert len(results) == 2

        # Verify first result is the image
        image_obj, image_file_obj = results[0]
        assert image_obj.id == "image_123"
        assert isinstance(image_file_obj, ImageFile)
        assert image_file_obj.path == "/path/to/image.jpg"

        # Verify second result is the scene
        scene_obj, video_file_obj = results[1]
        assert scene_obj.id == "scene_123"
        assert isinstance(video_file_obj, VideoFile)
        assert video_file_obj.path == "/path/to/video.mp4"

    @pytest.mark.asyncio
    async def test_find_stash_files_by_path(self, respx_stash_processor):
        """Test _find_stash_files_by_path method.

        Unit test using respx - tests path-based lookup with GraphQL filters.
        """
        # Expected GraphQL call sequence (will verify with debug):
        # 1: findImages with path filter containing media ID

        # Response 1: findImages - return images with path filter match
        image_file = {
            "__typename": "ImageFile",
            "id": "file_test123",
            "path": "/path/to/media_test123.jpg",
            "basename": "media_test123.jpg",
            "size": 1024,
            "width": 1920,
            "height": 1080,
            "format": "jpg",
            "parent_folder_id": "folder_123",
            "fingerprints": [],
            "mod_time": "2024-01-01T00:00:00Z",
        }
        image_result = create_image_dict(
            id="img_test123",
            title="Test Image",
            visual_files=[image_file],
        )
        images_result = create_find_images_result(
            count=1,
            images=[image_result],
            megapixels=2.07,  # 1920 * 1080 / 1000000
            filesize=1024,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImages", images_result)
                ),
            ]
        )

        # Test with a media file list
        media_files = [("test123", "image/jpeg")]

        # Call method
        results = await respx_stash_processor._find_stash_files_by_path(media_files)

        # Verify results
        assert isinstance(results, list)
        assert len(results) == 1

        # Verify result structure
        image_obj, file_obj = results[0]
        assert image_obj.id == "img_test123"
        assert isinstance(file_obj, ImageFile)
        assert file_obj.path == "/path/to/media_test123.jpg"
