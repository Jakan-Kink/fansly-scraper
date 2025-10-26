"""Performance tests for download operations."""

import asyncio
from pathlib import Path

import psutil
import pytest
from loguru import logger

from config import FanslyConfig


# Mock functions for testing - replace with actual imports when modules are implemented
async def download_media(
    url: str, media_id: str, save_path: Path, config: FanslyConfig
) -> bool:
    """Mock function for testing."""
    save_path.write_bytes(b"mock download content")
    return True


def process_media(
    input_path: Path, output_path: Path = None, config: FanslyConfig = None
):
    """Mock function for testing."""

    class Result:
        success: bool = True
        duration: float = 1.0
        media_info: dict = {"type": "video"}

    if output_path:
        output_path.write_bytes(b"mock processed content")
    return Result()


@pytest.mark.performance
async def test_concurrent_downloads_performance(
    test_config: FanslyConfig,
    test_downloads_dir: Path,
    performance_tracker,
    performance_threshold,
):
    """Test performance of concurrent downloads."""
    # Setup test data
    num_files = 5
    test_urls = [f"https://example.com/test{i}.mp4" for i in range(num_files)]

    async with performance_tracker("concurrent_downloads") as metrics:
        try:
            # Create download tasks
            tasks = []
            for i, url in enumerate(test_urls):
                save_path = test_downloads_dir / f"test{i}.mp4"
                task = asyncio.create_task(
                    download_media(
                        url=url,
                        media_id=str(i),
                        save_path=save_path,
                        config=test_config,
                    )
                )
                tasks.append(task)

            # Execute concurrent downloads and verify all succeeded
            download_results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(download_results):
                if isinstance(result, Exception):
                    pytest.fail(f"Download {i} failed: {result}")
                assert result, f"Download {i} failed without exception"

            # Verify performance
            assert metrics["duration"] <= performance_threshold["max_download_time"], (
                f"Concurrent downloads took too long: {metrics['duration']:.2f}s"
            )

            # Check memory usage
            assert metrics["max_memory"] <= performance_threshold["max_memory_mb"], (
                f"Memory usage too high: {metrics['max_memory']:.2f}MB"
            )

        except Exception as e:
            logger.error(f"Concurrent downloads performance test failed: {e}")
            raise


@pytest.mark.performance
def test_media_processing_performance(
    test_config: FanslyConfig,
    test_downloads_dir: Path,
    performance_tracker,
    performance_threshold,
):
    """Test performance of media processing operations."""
    # Setup test data
    input_file = test_downloads_dir / "perf_test_input.mp4"
    output_file = test_downloads_dir / "perf_test_output.mp4"

    try:
        # Create dummy test file (1MB)
        input_file.write_bytes(b"0" * (1024 * 1024))

        with performance_tracker("media_processing") as metrics:
            # Process media and verify success
            processing_result = process_media(
                input_path=input_file, output_path=output_file, config=test_config
            )
            assert processing_result.success, "Media processing failed"
            assert processing_result.duration > 0, "Invalid processing duration"

            # Verify performance
            assert metrics["duration"] <= performance_threshold["max_response_time"], (
                f"Media processing took too long: {metrics['duration']:.2f}s"
            )

            assert metrics["memory_change"] <= 100, (
                f"Memory usage increase too high: {metrics['memory_change']:.2f}MB"
            )

    except Exception as e:
        logger.error(f"Media processing performance test failed: {e}")
        raise
    finally:
        # Cleanup
        for file in [input_file, output_file]:
            if file.exists():
                file.unlink()


@pytest.mark.performance
@pytest.mark.parametrize("file_size_mb", [1, 10, 50])
def test_memory_scaling_performance(
    file_size_mb: int,
    test_config: FanslyConfig,
    test_downloads_dir: Path,
    performance_tracker,
    performance_threshold,
):
    """Test memory usage scaling with different file sizes."""
    input_file = test_downloads_dir / f"scaling_test_{file_size_mb}mb.mp4"
    output_file = test_downloads_dir / f"scaling_output_{file_size_mb}mb.mp4"

    try:
        # Create test file of specified size
        input_file.write_bytes(b"0" * (file_size_mb * 1024 * 1024))

        # Measure baseline memory before processing
        process = psutil.Process()
        baseline_memory = process.memory_info().rss / 1024 / 1024  # Convert to MB

        with performance_tracker(f"memory_scaling_{file_size_mb}mb") as metrics:
            # Process media and verify success
            processing_result = process_media(
                input_path=input_file, output_path=output_file, config=test_config
            )
            assert processing_result.success, (
                f"Media processing failed for {file_size_mb}MB file"
            )
            assert processing_result.duration > 0, (
                f"Invalid processing duration for {file_size_mb}MB file"
            )

            # Verify memory scaling - focusing on the increase over baseline
            processing_factor = 2  # Allow 2x file size for processing
            max_allowed_increase = file_size_mb * processing_factor

            # Calculate actual memory increase from baseline
            actual_increase = metrics["max_memory"] - baseline_memory

            assert actual_increase <= max_allowed_increase, (
                f"Memory increase ({actual_increase:.2f}MB) too high "
                f"for {file_size_mb}MB file (expected <= {max_allowed_increase}MB)"
            )

            # Also log the baseline for diagnostics
            logger.info(
                f"Baseline memory: {baseline_memory:.2f}MB, "
                f"Max memory: {metrics['max_memory']:.2f}MB, "
                f"Increase: {actual_increase:.2f}MB"
            )

    except Exception as e:
        logger.error(f"Memory scaling test failed for {file_size_mb}MB: {e}")
        raise
    finally:
        # Cleanup
        for file in [input_file, output_file]:
            if file.exists():
                file.unlink()
