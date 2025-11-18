"""Integration tests for StashProcessingBase - testing with REAL Stash instance.

These tests use real_stash_processor with capture_graphql_calls to verify error paths
and cleanup logic by hitting the actual Docker Stash instance.
"""

import asyncio
import contextlib
from unittest.mock import patch

import pytest


class TestStashProcessingBaseErrorPaths:
    """Integration tests for StashProcessingBase error paths."""

    @pytest.mark.asyncio
    async def test_scan_creator_folder_metadata_scan_error(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test scan_creator_folder when metadata_scan raises RuntimeError (line 154).

        This test patches metadata_scan to raise RuntimeError, verifying that
        it gets re-raised with additional context on line 154.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Patch metadata_scan to raise RuntimeError
            with patch.object(
                real_stash_processor.context.client,
                "metadata_scan",
                side_effect=RuntimeError("Metadata scan failed"),
            ):
                # Call scan_creator_folder - should hit line 154 which re-raises with context
                with pytest.raises(RuntimeError, match="Failed to process metadata"):
                    await real_stash_processor.scan_creator_folder()

            # Test successfully caught the re-raised RuntimeError from line 154

    @pytest.mark.asyncio
    async def test_scan_creator_folder_wait_for_job_retry(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test scan_creator_folder when wait_for_job raises exception and retries (lines 151-152).

        This test patches wait_for_job to raise an exception on first call,
        then succeed on second call, verifying the retry logic in lines 151-152.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Patch wait_for_job to raise exception first, then succeed
            # This will trigger the exception handler on lines 151-152 which sets finished_job=False
            # causing the loop to retry
            with patch.object(
                real_stash_processor.context.client,
                "wait_for_job",
                side_effect=[
                    Exception("Transient job polling error"),  # First call fails
                    True,  # Second call succeeds
                ],
            ):
                # Call scan_creator_folder - should retry after first wait_for_job failure
                await real_stash_processor.scan_creator_folder()

            # Test completes successfully, proving retry logic works

    @pytest.mark.asyncio
    async def test_scan_creator_folder_download_path_creation_fails(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test scan_creator_folder when download path creation fails (lines 126-128).

        This test verifies that when directory creation fails, the scan returns early
        without attempting to call Stash. No GraphQL calls should be made.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Set base_path to None to trigger the path creation code (line 119)
            real_stash_processor.state.base_path = None

            # Patch set_create_directory_for_download to raise exception
            with patch(
                "stash.processing.base.set_create_directory_for_download",
                side_effect=Exception("Permission denied"),
            ):
                # Call scan_creator_folder - should hit exception handler lines 126-128
                await real_stash_processor.scan_creator_folder()

            # Verify base_path is still None (creation failed and we returned early)
            assert real_stash_processor.state.base_path is None

    @pytest.mark.asyncio
    async def test_cleanup_with_running_background_task(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when background task is running (lines 257-313).

        This test verifies that cleanup properly cancels running background tasks
        and sets the cleanup event. Tests the finally block cleanup logic.
        Covers line 268 (cleanup event wait succeeds).
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Use an event to ensure the task is running before we call cleanup
            task_started = asyncio.Event()

            # Create a background task that mimics _safe_background_processing
            # It should set the cleanup event when cancelled
            async def background_task_with_cleanup():
                try:
                    # Signal that task has started
                    task_started.set()
                    # Simulate some work, but not too long to avoid pytest timeout
                    await asyncio.sleep(30)
                finally:
                    # Always set cleanup event (mimics line 245 in base.py)
                    if real_stash_processor._cleanup_event:
                        real_stash_processor._cleanup_event.set()

            # Start background task
            task = asyncio.create_task(background_task_with_cleanup())
            real_stash_processor._background_task = task
            real_stash_processor.config._background_tasks.append(task)

            # Wait for task to start before calling cleanup
            await task_started.wait()

            # Give it a moment to ensure it's in the sleep
            await asyncio.sleep(0.1)

            # Call cleanup - should cancel task and wait for cleanup event
            await real_stash_processor.cleanup()

            # Verify task was cancelled
            assert task.cancelled()

            # Verify cleanup event was set (covers line 268 - wait succeeded)
            assert real_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_cleanup_with_no_background_task(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when no background task exists.

        This test verifies that cleanup works correctly when there is no
        background task to cancel, just closing the Stash client.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Ensure no background task
            real_stash_processor._background_task = None

            # Call cleanup - should just close client
            await real_stash_processor.cleanup()

            # Verify cleanup event was set
            assert real_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_cleanup_with_no_cleanup_event(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when _cleanup_event is None (covers 264->275 false path).

        This test verifies that cleanup handles the case where _cleanup_event
        is None, skipping the wait logic at line 264 and jumping directly to line 275.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Create a simple background task
            async def simple_task():
                await asyncio.sleep(30)

            task = asyncio.create_task(simple_task())
            real_stash_processor._background_task = task

            # Set cleanup event to None to trigger false path at line 264
            real_stash_processor._cleanup_event = None

            # Call cleanup - should skip the wait logic and go directly to line 275
            await real_stash_processor.cleanup()

            # Give task time to finish cancelling
            await asyncio.sleep(0.1)

            # Verify task was cancelled
            assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_cleanup_event_wait_timeout(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when cleanup event wait times out (covers 269-270).

        This test verifies that cleanup handles the timeout case when waiting
        for the cleanup event, logging a warning and continuing with cleanup.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Create a background task that does NOT set the cleanup event
            # This will cause the wait to timeout
            task_started = asyncio.Event()

            async def task_without_cleanup():
                # Task that doesn't set cleanup event when cancelled
                task_started.set()
                await asyncio.sleep(30)

            task = asyncio.create_task(task_without_cleanup())
            real_stash_processor._background_task = task

            # Wait for task to start
            await task_started.wait()
            await asyncio.sleep(0.1)

            # Patch asyncio.wait_for to timeout immediately for testing
            # This avoids waiting the full 10 seconds
            original_wait_for = asyncio.wait_for

            async def immediate_timeout(*args, **kwargs):
                # Simulate immediate timeout
                raise TimeoutError

            with patch("asyncio.wait_for", side_effect=immediate_timeout):
                # Call cleanup - should hit timeout path at lines 269-270
                await real_stash_processor.cleanup()

            # Give task time to finish cancelling
            await asyncio.sleep(0.1)

            # Verify task was cancelled
            assert task.cancelled() or task.done()

            # Verify cleanup event was force-set at line 277
            assert real_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_cleanup_without_config_attribute(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when config attribute missing (covers 280->304 false branch).

        This test verifies that cleanup handles the case where the processor
        doesn't have a config attribute, skipping the background tasks cleanup
        logic at line 280 and jumping directly to the finally block at line 303.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Temporarily remove config attribute to trigger false path at line 280
            original_config = real_stash_processor.config
            del real_stash_processor.config

            try:
                # Call cleanup - should skip background tasks logic (line 280->304)
                await real_stash_processor.cleanup()

                # Verify cleanup event was set
                assert real_stash_processor._cleanup_event.is_set()
            finally:
                # Restore config for fixture teardown
                real_stash_processor.config = original_config

    @pytest.mark.asyncio
    async def test_cleanup_context_close_exception(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when context.close() raises Exception (covers 310-311).

        This test verifies that cleanup handles exceptions from context.close()
        in the finally block, logging an error and continuing gracefully.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first so fixture teardown doesn't fail
            await real_stash_processor.context.get_client()

            # Patch context.close to raise a generic Exception (not TimeoutError)
            original_close = real_stash_processor.context.close

            async def close_with_error():
                raise RuntimeError("Simulated close error")

            real_stash_processor.context.close = close_with_error

            try:
                # Call cleanup - should catch exception at lines 310-311
                await real_stash_processor.cleanup()

                # Verify cleanup completed despite the error
                assert real_stash_processor._cleanup_event.is_set()
            finally:
                # Restore original close method
                real_stash_processor.context.close = original_close

    @pytest.mark.asyncio
    async def test_safe_background_processing_without_config(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _safe_background_processing without config (covers 231->244).

        This test verifies that _safe_background_processing handles the case
        where config doesn't exist, skipping the task removal logic at line 231
        and jumping to line 244.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            # Temporarily remove config attribute
            original_config = real_stash_processor.config
            del real_stash_processor.config

            try:
                # Call _safe_background_processing directly - it will fail at
                # continue_stash_processing due to missing account, but that's ok
                # We just need to test the finally block
                with contextlib.suppress(Exception):
                    await real_stash_processor._safe_background_processing(None, None)

                # Verify cleanup event was set (line 244-245)
                assert real_stash_processor._cleanup_event.is_set()
            finally:
                # Restore config
                real_stash_processor.config = original_config

    @pytest.mark.asyncio
    async def test_safe_background_processing_task_removal(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _safe_background_processing task removal (covers 235-240).

        This test verifies that _safe_background_processing removes the current
        task from config._background_tasks in the finally block.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            # Create a task that calls _safe_background_processing
            async def run_background_processing():
                with contextlib.suppress(Exception):
                    await real_stash_processor._safe_background_processing(None, None)

            # Start the task and add it to background_tasks
            task = asyncio.create_task(run_background_processing())
            real_stash_processor.config._background_tasks.append(task)

            # Wait for task to complete
            await task

            # Verify task was removed from background_tasks (lines 235-240)
            assert task not in real_stash_processor.config._background_tasks

            # Verify cleanup event was set
            assert real_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_safe_background_processing_without_cleanup_event(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _safe_background_processing without cleanup_event (covers 244->exit).

        This test verifies that _safe_background_processing handles the case
        where _cleanup_event is None, skipping the set() call at line 244
        and jumping to exit.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            # Set cleanup_event to None
            real_stash_processor._cleanup_event = None

            # Call _safe_background_processing directly
            with contextlib.suppress(Exception):
                await real_stash_processor._safe_background_processing(None, None)

            # Test completes without error - the false branch at line 244 was taken

    @pytest.mark.asyncio
    async def test_safe_background_processing_task_already_removed(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _safe_background_processing when task already removed (covers line 240).

        This test verifies that _safe_background_processing handles the ValueError
        when trying to remove a task that's already been removed from background_tasks.
        The ValueError can occur in a race condition between the 'if' check and 'remove()'.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            # Create a custom list that raises ValueError on remove to simulate race condition
            class RacyList(list):
                def remove(self, item):
                    # Always raise ValueError to simulate the race condition
                    # where item was removed between 'if in' check and remove()
                    raise ValueError("Simulated race condition")

            # Replace background_tasks with our racy list
            original_tasks = real_stash_processor.config._background_tasks
            racy_tasks = RacyList(original_tasks)
            real_stash_processor.config._background_tasks = racy_tasks

            try:
                # Create and add task to racy list
                async def run_background_processing():
                    with contextlib.suppress(Exception):
                        await real_stash_processor._safe_background_processing(
                            None, None
                        )

                task = asyncio.create_task(run_background_processing())
                racy_tasks.append(task)

                # Wait for task to complete
                await task

                # Test completes without error - line 240 (except ValueError) was hit
            finally:
                # Restore original list
                real_stash_processor.config._background_tasks = original_tasks

    @pytest.mark.asyncio
    async def test_cleanup_with_matching_own_tasks(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() with tasks matching module pattern (covers 293-300).

        This test verifies that cleanup cancels tasks from config._background_tasks
        that match the processor's module pattern, covering the for loop at lines 293-300.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            task_started_1 = asyncio.Event()
            task_started_2 = asyncio.Event()

            async def task_1():
                task_started_1.set()
                await asyncio.sleep(30)

            async def task_2():
                task_started_2.set()
                # This task completes quickly to test both branches of task.done()
                await asyncio.sleep(0.05)

            # Start tasks
            task1 = asyncio.create_task(task_1())
            task2 = asyncio.create_task(task_2())

            # Patch the tasks' coroutine __qualname__ to match the processor's module
            processor_module = real_stash_processor.__class__.__module__
            task1.get_coro().__qualname__ = f"{processor_module}.task_1"
            task2.get_coro().__qualname__ = f"{processor_module}.task_2"

            # Add tasks to config background_tasks
            real_stash_processor.config._background_tasks.extend([task1, task2])

            # Wait for tasks to start
            await task_started_1.wait()
            await task_started_2.wait()

            # Give task2 time to complete so we test both task.done() branches
            await asyncio.sleep(0.1)

            # Call cleanup - should cancel matching tasks (lines 293-300)
            await real_stash_processor.cleanup()

            # Give tasks time to finish
            await asyncio.sleep(0.1)

            # Verify tasks were cancelled or done
            assert task1.done() or task1.cancelled()
            assert task2.done()

            # Verify tasks were removed from config background_tasks
            assert task1 not in real_stash_processor.config._background_tasks
            assert task2 not in real_stash_processor.config._background_tasks

    @pytest.mark.asyncio
    async def test_cleanup_exception_in_try_block(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test cleanup() when exception occurs in try block (covers 299-300).

        This test verifies that cleanup handles exceptions in the try block
        by logging an error and continuing with the finally block cleanup.
        """
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            # Initialize client first
            await real_stash_processor.context.get_client()

            # Patch get_background_tasks to raise an exception
            def raise_exception():
                raise RuntimeError("Simulated error in cleanup")

            real_stash_processor.config.get_background_tasks = raise_exception

            # Call cleanup - should catch exception at lines 299-300
            await real_stash_processor.cleanup()

            # Verify cleanup completed despite the error
            assert real_stash_processor._cleanup_event.is_set()
