"""Fixtures and configuration for performance tests."""

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter, time

import psutil
import pytest


@pytest.fixture(scope="session")
def performance_log_dir():
    """Create a directory for performance test logs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture(scope="session")
def performance_threshold():
    """Define performance thresholds for tests."""
    return {
        "max_memory_mb": 512,  # Maximum memory usage in MB
        "max_cpu_percent": 80,  # Maximum CPU usage percentage
        "max_response_time": 2.0,  # Maximum response time in seconds
        "max_download_time": 30.0,  # Maximum download time in seconds
    }


@contextmanager
def track_performance() -> Generator[dict, None, None]:
    """Context manager to track performance metrics."""
    start_time = perf_counter()
    process = psutil.Process()
    start_memory = process.memory_info().rss / 1024 / 1024  # Convert to MB

    metrics = {
        "start_time": time(),
        "start_memory": start_memory,
        "max_memory": start_memory,
        "max_cpu": 0.0,
    }

    try:
        yield metrics
    finally:
        end_time = perf_counter()
        end_memory = process.memory_info().rss / 1024 / 1024

        metrics.update(
            {
                "duration": end_time - start_time,
                "memory_change": end_memory - start_memory,
                "end_memory": end_memory,
                "end_time": time(),
            }
        )


@pytest.fixture
def performance_tracker(performance_log_dir, request):
    """Fixture to track and log performance metrics."""

    def _track_performance(operation_name: str):
        log_file = performance_log_dir / f"{request.node.name}_{operation_name}.log"

        with track_performance() as metrics:
            yield metrics

            # Log performance data
            with open(log_file, "a") as f:
                f.write(f"Performance metrics for {operation_name}:\n")
                f.write(f"Duration: {metrics['duration']:.3f} seconds\n")
                f.write(f"Memory change: {metrics['memory_change']:.2f} MB\n")
                f.write(f"Max memory: {metrics['max_memory']:.2f} MB\n")
                f.write(f"Max CPU: {metrics['max_cpu']:.1f}%\n")
                f.write("-" * 50 + "\n")

    return _track_performance


@pytest.fixture(autouse=True)
def check_performance(performance_threshold, request):
    """Automatically check performance metrics after each test."""
    yield

    # Skip performance checks for tests marked as slow
    if request.node.get_closest_marker("slow"):
        return

    process = psutil.Process()
    current_memory = process.memory_info().rss / 1024 / 1024
    current_cpu = process.cpu_percent()

    # Assert performance thresholds
    assert current_memory <= performance_threshold["max_memory_mb"], (
        f"Memory usage ({current_memory:.2f} MB) exceeds threshold "
        f"({performance_threshold['max_memory_mb']} MB)"
    )

    assert current_cpu <= performance_threshold["max_cpu_percent"], (
        f"CPU usage ({current_cpu:.1f}%) exceeds threshold "
        f"({performance_threshold['max_cpu_percent']}%)"
    )
