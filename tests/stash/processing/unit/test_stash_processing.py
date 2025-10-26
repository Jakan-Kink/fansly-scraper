"""Unit tests for stash processing module - core functionality."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stash.context import StashContext
from stash.processing import StashProcessing
from stash.types import Image
from tests.fixtures import AccountFactory, PerformerFactory
from tests.fixtures.database_fixtures import AwaitableAttrsMock


# Most fixtures are imported from tests.fixtures via conftest.py:
# - mock_config, mock_state, mock_context, mock_database (from stash_integration_fixtures)
# - stash_processor (for integration tests)
#
# For unit tests, we define a simple processor fixture below that uses mocked dependencies


@pytest.fixture
def processor(mock_config, mock_state, mock_context, mock_database):
    """Fixture for stash processor instance for unit testing.

    This creates a StashProcessing instance with all dependencies mocked.
    For integration tests, use the 'stash_processor' fixture instead.
    """
    processor = StashProcessing(
        config=mock_config,
        state=mock_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )
    return processor


class TestStashProcessingBasics:
    """Test the basic functionality of StashProcessing class."""

    def test_init(self, mock_config, mock_state, mock_context, mock_database):
        """Test initialization of StashProcessing."""
        # Create without background task
        processor = StashProcessing(
            config=mock_config,
            state=mock_state,
            context=mock_context,
            database=mock_database,
            _background_task=None,
            _cleanup_event=None,
            _owns_db=False,
        )

        # Verify attributes
        assert processor.config == mock_config
        assert processor.state == mock_state
        assert processor.context == mock_context
        assert processor.database == mock_database
        assert processor._background_task is None
        assert not processor._cleanup_event.is_set()
        assert not processor._owns_db
        assert isinstance(processor.log, logging.Logger)

        # Create with background task
        mock_task = MagicMock()
        processor = StashProcessing(
            config=mock_config,
            state=mock_state,
            context=mock_context,
            database=mock_database,
            _background_task=mock_task,
            _cleanup_event=None,
            _owns_db=True,
        )

        # Verify attributes
        assert processor._background_task == mock_task
        assert not processor._cleanup_event.is_set()
        assert processor._owns_db

    def test_from_config(self, mock_config, mock_state):
        """Test creating processor from config."""
        # Mock get_stash_context
        mock_context = MagicMock(spec=StashContext)
        mock_config.get_stash_context.return_value = mock_context

        # Call from_config
        processor = StashProcessing.from_config(
            config=mock_config,
            state=mock_state,
        )

        # Verify processor
        assert processor.config == mock_config
        assert processor.state is not mock_state  # Should be a copy
        assert processor.state.creator_id == mock_state.creator_id
        assert processor.state.creator_name == mock_state.creator_name
        assert processor.context == mock_context
        assert processor.database == mock_config._database
        assert processor._background_task is None
        assert not processor._cleanup_event.is_set()
        assert not processor._owns_db


class TestStashProcessingAccount:
    """Test the account-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_account(self, processor):
        """Test _find_account method."""
        # Create test account using factory
        test_account = AccountFactory.build(
            id=12345,
            username="test_user",
        )

        # Mock session and execute - needs to be AsyncMock for async session
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = test_account
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Call _find_account
        account = await processor._find_account(session=mock_session)

        # Verify account and session.execute was called
        assert account == test_account
        assert account.id == 12345
        assert account.username == "test_user"
        mock_session.execute.assert_called_once()

        # Test with no account found
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.reset_mock()

        # Call _find_account
        with patch(
            "stash.processing.mixins.account.print_warning"
        ) as mock_print_warning:
            account = await processor._find_account(session=mock_session)

        # Verify no account and warning was printed
        assert account is None
        mock_print_warning.assert_called_once()
        assert processor.state.creator_name in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_update_account_stash_id(self, processor):
        """Test _update_account_stash_id method."""
        # Create test account using factory
        test_account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        test_account.stash_id = None  # Start with no stash_id

        # Create test performer using factory
        mock_performer = PerformerFactory(
            id="123",  # Use numeric string since code converts to int
            name="test_user",
        )

        # Create mock session with async execute
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = test_account
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        # Call _update_account_stash_id
        await processor._update_account_stash_id(
            test_account, mock_performer, session=mock_session
        )

        # Verify session operations
        mock_session.execute.assert_called_once()
        assert str(test_account.id) in str(mock_session.execute.call_args)
        # The account's stash_id should be updated to the int value of performer.id
        assert test_account.stash_id == int(mock_performer.id)
        mock_session.add.assert_called_once_with(test_account)
        mock_session.flush.assert_called_once()


class TestStashProcessingPerformer:
    """Test the performer-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, processor):
        """Test _find_existing_performer method."""
        # Create test performer using factory
        mock_performer = PerformerFactory(
            id="performer_123",
            name="test_user",
        )

        # Mock context.client.find_performer
        # Important: The method awaits find_performer, so it should return the performer directly
        processor.context.client.find_performer = AsyncMock(return_value=mock_performer)

        # Clear the cache before testing
        if hasattr(processor._find_existing_performer, "cache_clear"):
            processor._find_existing_performer.cache_clear()

        # Case 1: Account has stash_id
        # Create a new account object (different cache key from fixture)
        test_account_1 = AccountFactory.build(username="test_user")
        test_account_1.stash_id = "stash_123"

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(test_account_1)

        # Verify performer and find_performer was called with stash_id
        assert performer == mock_performer
        processor.context.client.find_performer.assert_called_once_with("stash_123")

        # Case 2: Account has no stash_id
        processor.context.client.find_performer.reset_mock()

        # Create a new account object (different cache key)
        test_account_2 = AccountFactory.build(username="test_user")
        test_account_2.stash_id = None

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(test_account_2)

        # Verify performer and find_performer was called with username
        assert performer == mock_performer
        processor.context.client.find_performer.assert_called_once_with(
            test_account_2.username
        )

        # Case 3: find_performer returns None
        processor.context.client.find_performer.reset_mock()
        processor.context.client.find_performer.return_value = None

        # Create another new account object (different cache key)
        test_account_3 = AccountFactory.build(username="test_user_2")
        test_account_3.stash_id = None

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(test_account_3)

        # Verify performer is None
        assert performer is None
        processor.context.client.find_performer.assert_called_once_with(
            test_account_3.username
        )

        # Case 4: find_performer returns a coroutine
        processor.context.client.find_performer.reset_mock()

        # Create a coroutine that returns mock_performer
        async def mock_coroutine():
            return mock_performer

        processor.context.client.find_performer.return_value = mock_coroutine()

        # Create another test account for this case
        test_account_4 = AccountFactory.build(username="test_user_3")
        test_account_4.stash_id = None

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(test_account_4)

        # Verify performer
        assert performer == mock_performer

    @pytest.mark.asyncio
    async def test_update_performer_avatar(self, processor):
        """Test _update_performer_avatar method."""
        # Create test performer using factory
        mock_performer = PerformerFactory(
            id="123",
            name="test_user",
        )
        # Mock update_avatar method since it's not part of the factory
        mock_performer.update_avatar = AsyncMock()

        # Case 1: Account with no avatar
        test_account_no_avatar = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        test_account_no_avatar.avatar = None
        test_account_no_avatar.awaitable_attrs = AwaitableAttrsMock(
            test_account_no_avatar
        )

        await processor._update_performer_avatar(test_account_no_avatar, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Case 2: Account with avatar but no local_filename
        test_account_no_file = AccountFactory.build(
            id=12346,
            username="test_user",
        )
        mock_avatar_no_file = MagicMock()
        mock_avatar_no_file.local_filename = None
        test_account_no_file.avatar = mock_avatar_no_file
        test_account_no_file.awaitable_attrs = AwaitableAttrsMock(test_account_no_file)

        await processor._update_performer_avatar(test_account_no_file, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Case 3: Account with avatar and local_filename - should update
        test_account_with_avatar = AccountFactory.build(
            id=12347,
            username="test_user",
        )
        mock_avatar_with_file = MagicMock()
        mock_avatar_with_file.local_filename = "avatar.jpg"
        test_account_with_avatar.avatar = mock_avatar_with_file
        test_account_with_avatar.awaitable_attrs = AwaitableAttrsMock(
            test_account_with_avatar
        )

        # Mock performer with default image
        mock_performer.image_path = "default=true"

        # Mock client.find_images
        mock_image = MagicMock(spec=Image)
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = "path/to/avatar.jpg"

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        processor.context.client.find_images = AsyncMock(return_value=mock_image_result)

        # Reset mock_performer.update_avatar for the test
        mock_performer.update_avatar = AsyncMock()

        await processor._update_performer_avatar(
            test_account_with_avatar, mock_performer
        )

        # Verify avatar update was attempted
        processor.context.client.find_images.assert_called_once()
        assert "avatar.jpg" in str(processor.context.client.find_images.call_args)
        mock_performer.update_avatar.assert_called_once()
        assert "path/to/avatar.jpg" in str(mock_performer.update_avatar.call_args)

        # Case 4: No images found in Stash
        processor.context.client.find_images.reset_mock()
        mock_image_result.count = 0
        processor.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()

        await processor._update_performer_avatar(
            test_account_with_avatar, mock_performer
        )

        # Verify no avatar update was attempted
        processor.context.client.find_images.assert_called_once()
        assert not mock_performer.update_avatar.called

        # Case 5: update_avatar raises exception
        processor.context.client.find_images.reset_mock()
        mock_image_result.count = 1
        processor.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()
        mock_performer.update_avatar.side_effect = Exception("Test error")

        # Mock print_error and logger
        with (
            patch("stash.processing.print_error") as mock_print_error,
            patch("stash.processing.logger.exception") as mock_logger_exception,
            patch("stash.processing.debug_print") as mock_debug_print,
        ):
            # Call _update_performer_avatar
            await processor._update_performer_avatar(
                test_account_with_avatar, mock_performer
            )

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to update performer avatar" in str(
                mock_print_error.call_args
            )
            mock_logger_exception.assert_called_once()
            mock_debug_print.assert_called_once()
            assert "avatar_update_failed" in str(mock_debug_print.call_args)
