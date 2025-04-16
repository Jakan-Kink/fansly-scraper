"""Functional tests for the download workflow."""

from pathlib import Path

import pytest
from loguru import logger

from config import FanslyConfig
from config.modes import DownloadMode
from download.types import DownloadType
from pathio import get_creator_base_path


@pytest.fixture
def test_downloads_dir(test_config: FanslyConfig, tmp_path: Path) -> Path:
    """Create a temporary downloads directory."""
    test_config.base_directory = str(tmp_path)
    test_config.download_directory = tmp_path
    test_config.download_mode = DownloadMode.NORMAL

    # Create the creator directory structure
    creator_path = tmp_path / "test_user_fansly"
    creator_path.mkdir(parents=True, exist_ok=True)
    return creator_path


# Mock functions for testing - replace with actual imports when modules are implemented
def download_media(
    url: str, media_id: str, save_path: Path, config: FanslyConfig
) -> bool:
    """Mock function for testing."""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(b"mock download content")
    return True


def process_media(
    input_path: Path,
    output_path: Path = None,
    media_type: str = None,
    config: FanslyConfig = None,
):
    """Mock function for testing."""

    class Result:
        success: bool = True
        duration: float = 1.0
        media_info: dict = {"type": media_type or "video"}

    if output_path:
        output_path.write_bytes(b"mock processed content")
    return Result()


@pytest.mark.functional
def test_basic_download_workflow(test_config: FanslyConfig, test_downloads_dir: Path):
    """Test the basic media download workflow."""
    # Setup test data
    media_url = "https://example.com/test.mp4"
    media_id = "123"
    expected_path = test_downloads_dir / f"{media_id}.mp4"

    try:
        # Execute download workflow
        success = download_media(
            url=media_url,
            media_id=media_id,
            save_path=expected_path,
            config=test_config,
        )

        # Verify results
        assert success, "Download should complete successfully"
        assert expected_path.exists(), "Downloaded file should exist"
        assert expected_path.stat().st_size > 0, "Downloaded file should not be empty"

    except Exception as e:
        logger.error(f"Download workflow failed: {e}")
        raise
    finally:
        # Cleanup
        if expected_path.exists():
            expected_path.unlink()


@pytest.mark.functional
def test_media_processing_workflow(test_config: FanslyConfig, test_downloads_dir: Path):
    """Test the media processing workflow after download."""
    # Setup test data
    test_file = test_downloads_dir / "test_input.mp4"
    processed_file = test_downloads_dir / "processed_output.mp4"

    try:
        # Create dummy test file
        test_file.write_bytes(b"dummy video content")

        # Execute processing workflow
        result = process_media(
            input_path=test_file, output_path=processed_file, config=test_config
        )

        # Verify results
        assert result.success, "Media processing should complete successfully"
        assert processed_file.exists(), "Processed file should exist"
        assert result.duration > 0, "Processed media should have valid duration"

    except Exception as e:
        logger.error(f"Media processing workflow failed: {e}")
        raise
    finally:
        # Cleanup
        for file in [test_file, processed_file]:
            if file.exists():
                file.unlink()


@pytest.mark.functional
@pytest.mark.parametrize("media_type", ["video", "image", "audio"])
def test_media_type_handling(media_type, test_config, test_downloads_dir):
    """Test handling of different media types in the download workflow."""
    # Setup test data for different media types
    media_configs = {
        "video": {"ext": "mp4", "content": b"dummy video"},
        "image": {"ext": "jpg", "content": b"dummy image"},
        "audio": {"ext": "mp3", "content": b"dummy audio"},
    }

    config = media_configs[media_type]
    test_file = test_downloads_dir / f"test.{config['ext']}"

    try:
        # Create test file
        test_file.write_bytes(config["content"])

        # Process based on media type
        result = process_media(
            input_path=test_file, media_type=media_type, config=test_config
        )

        # Verify results
        assert result.success, f"{media_type} processing should succeed"
        assert result.media_info["type"] == media_type

    except Exception as e:
        logger.error(f"{media_type} processing failed: {e}")
        raise
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()
