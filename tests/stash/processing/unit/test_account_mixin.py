"""Unit tests for AccountProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from metadata import Account
from stash.processing.mixins.account import AccountProcessingMixin
from stash.types import Performer


# Note: These unit tests use Mock objects for Account instead of Factories because:
# 1. These are pure unit tests that don't interact with a database
# 2. The code needs to mock async properties (awaitable_attrs) that real Account
#    objects can't provide without a database session
# 3. Using Mocks is appropriate here since we're testing the mixin logic in isolation
# 4. Integration tests would use real Account instances from Factories


class TestMixinClass(AccountProcessingMixin):
    """Test class that implements AccountProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.state = MagicMock()
        self.state.creator_id = "12345"
        self.state.creator_name = "test_user"
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.log = MagicMock()


@pytest.fixture
def mixin():
    """Fixture for AccountProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def test_account():
    """Fixture for mock account.

    Uses MagicMock instead of Factory because:
    - Unit test without database
    - Needs to mock async properties (awaitable_attrs)
    - Real Account requires database session for properties
    """
    account = MagicMock(spec=Account)
    account.id = 12345
    account.username = "test_user"
    account.stash_id = "stash_123"
    # Setup awaitable_attrs for async property access
    account.awaitable_attrs = MagicMock()
    # Make sure the avatar is properly mockable
    avatar_mock = MagicMock()
    avatar_mock.local_filename = "avatar.jpg"
    account.awaitable_attrs.avatar = avatar_mock
    return account


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


class TestAccountProcessingMixin:
    """Test the account processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_account(self, mixin, test_account):
        """Test _find_account method."""
        # Mock session and execute
        mock_session = MagicMock(spec=Session)
        mock_session.execute = AsyncMock()
        mock_session.execute.return_value.scalar_one_or_none = AsyncMock(
            return_value=test_account
        )

        # Call _find_account with creator_id
        account = await mixin._find_account(session=mock_session)

        # Verify account and session.execute was called
        assert account == test_account
        mock_session.execute.assert_called_once()
        assert "12345" in str(mock_session.execute.call_args)

        # Test with creator_name instead of id
        mixin.state.creator_id = None
        mock_session.execute.reset_mock()

        # Call _find_account again
        account = await mixin._find_account(session=mock_session)

        # Verify session.execute was called with username condition
        assert account == test_account
        mock_session.execute.assert_called_once()
        assert "test_user" in str(mock_session.execute.call_args.args[0])

        # Test with no account found
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        mock_session.execute.reset_mock()

        # Call _find_account
        with patch(
            "stash.processing.mixins.account.print_warning"
        ) as mock_print_warning:
            account = await mixin._find_account(session=mock_session)

        # Verify no account and warning was printed
        assert account is None
        mock_print_warning.assert_called_once()
        assert "test_user" in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_process_creator(self, mixin, test_account, mock_performer):
        """Test process_creator method."""
        # Mock methods
        mixin._find_account = AsyncMock(return_value=test_account)
        mixin._find_existing_performer = AsyncMock(return_value=mock_performer)
        mixin._update_performer_avatar = AsyncMock()

        # Mock session
        mock_session = MagicMock(spec=Session)

        # Call process_creator
        account, performer = await mixin.process_creator(session=mock_session)

        # Verify calls and results
        mixin._find_account.assert_called_once_with(mock_session)
        mixin._find_existing_performer.assert_called_once_with(test_account)
        mixin._update_performer_avatar.assert_called_once_with(
            test_account, mock_performer
        )
        assert account == test_account
        assert performer == mock_performer

        # Test with no existing performer (creates new one)
        mixin._find_account.reset_mock()
        mixin._find_existing_performer.reset_mock()
        mixin._update_performer_avatar.reset_mock()
        mixin._find_existing_performer.return_value = None

        # Mock performer creation
        mock_new_performer = MagicMock(spec=Performer)
        with patch(
            "stash.types.Performer.from_account", return_value=mock_new_performer
        ) as mock_from_account:
            # Call process_creator
            account, performer = await mixin.process_creator(session=mock_session)

            # Verify performer creation
            mock_from_account.assert_called_once_with(test_account)
            mock_new_performer.save.assert_called_once_with(mixin.context.client)
            mixin._update_performer_avatar.assert_called_once_with(
                test_account, mock_new_performer
            )
            assert account == test_account
            assert performer == mock_new_performer

        # Test with no account found
        mixin._find_account.return_value = None

        # Call process_creator and expect error
        with pytest.raises(ValueError) as excinfo:
            await mixin.process_creator(session=mock_session)

        # Verify error message
        assert "No account found for creator" in str(excinfo.value)
        assert "test_user" in str(excinfo.value)

        # Test exception handling
        mixin._find_account.side_effect = Exception("Test error")

        # Call process_creator with error mocks
        with (
            pytest.raises(Exception),
            patch("stash.processing.mixins.account.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.account.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.account.debug_print") as mock_debug_print,
        ):
            await mixin.process_creator(session=mock_session)

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to process creator" in str(mock_print_error.call_args)
            mock_logger_exception.assert_called_once()
            mock_debug_print.assert_called_once()
            assert "creator_processing_failed" in str(mock_debug_print.call_args)

    @pytest.mark.asyncio
    async def test_update_performer_avatar(self, mixin, test_account, mock_performer):
        """Test _update_performer_avatar method."""
        # Mock account with no avatar
        test_account.awaitable_attrs = MagicMock()
        test_account.awaitable_attrs.avatar = None

        # Call _update_performer_avatar
        await mixin._update_performer_avatar(test_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar but no local_filename
        mock_avatar = MagicMock()
        mock_avatar.local_filename = None
        test_account.awaitable_attrs.avatar = mock_avatar

        # Call _update_performer_avatar
        await mixin._update_performer_avatar(test_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar and local_filename
        mock_avatar.local_filename = "avatar.jpg"
        test_account.awaitable_attrs.avatar = mock_avatar

        # Mock performer with default image
        mock_performer.image_path = "default=true"

        # Mock client.find_images
        mock_image = MagicMock()
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = "path/to/avatar.jpg"

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        mixin.context.client.find_images = AsyncMock(return_value=mock_image_result)

        # Mock performer.update_avatar
        mock_performer.update_avatar = AsyncMock()

        # Call _update_performer_avatar
        await mixin._update_performer_avatar(test_account, mock_performer)

        # Verify avatar update was attempted
        mixin.context.client.find_images.assert_called_once()
        assert "avatar.jpg" in str(mixin.context.client.find_images.call_args)
        mock_performer.update_avatar.assert_called_once()
        assert "path/to/avatar.jpg" in str(mock_performer.update_avatar.call_args)

        # Test with no images found
        mixin.context.client.find_images.reset_mock()
        mock_image_result.count = 0
        mixin.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()

        # Call _update_performer_avatar
        await mixin._update_performer_avatar(test_account, mock_performer)

        # Verify no avatar update was attempted
        mixin.context.client.find_images.assert_called_once()
        assert not mock_performer.update_avatar.called

        # Test with update_avatar raising exception
        mixin.context.client.find_images.reset_mock()
        mock_image_result.count = 1
        mixin.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()
        mock_performer.update_avatar.side_effect = Exception("Test error")

        # Mock print_error and logger
        with (
            patch("stash.processing.mixins.account.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.account.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.account.debug_print") as mock_debug_print,
        ):
            # Call _update_performer_avatar
            await mixin._update_performer_avatar(test_account, mock_performer)

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to update performer avatar" in str(
                mock_print_error.call_args
            )
            mock_logger_exception.assert_called_once()
            mock_debug_print.assert_called_once()
            assert "avatar_update_failed" in str(mock_debug_print.call_args)

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, mixin, test_account):
        """Test _find_existing_performer method."""
        # Mock context.client.find_performer
        mock_performer = MagicMock(spec=Performer)
        mixin.context.client.find_performer = AsyncMock(return_value=mock_performer)

        # Case 1: Account has stash_id
        test_account.stash_id = "stash_123"

        # Call _find_existing_performer
        performer = await mixin._find_existing_performer(test_account)

        # Verify performer and find_performer was called with stash_id
        assert performer == mock_performer
        mixin.context.client.find_performer.assert_called_once_with("stash_123")

        # Case 2: Account has no stash_id
        mixin.context.client.find_performer.reset_mock()
        test_account.stash_id = None

        # Debug: Check state before call
        print("\n=== DEBUG Case 2 ===")
        print(f"test_account.stash_id: {test_account.stash_id}")
        print(f"test_account.username: {test_account.username}")
        print(
            f"mock call_count before: {mixin.context.client.find_performer.call_count}"
        )

        # Call _find_existing_performer
        performer = await mixin._find_existing_performer(test_account)

        # Debug: Check state after call
        print(f"performer result: {performer}")
        print(
            f"mock call_count after: {mixin.context.client.find_performer.call_count}"
        )
        print(f"mock call_args: {mixin.context.client.find_performer.call_args}")

        # Verify performer and find_performer was called with username
        assert performer == mock_performer
        mixin.context.client.find_performer.assert_called_once_with(
            test_account.username
        )

        # Case 3: find_performer returns None
        mixin.context.client.find_performer.reset_mock()
        mixin.context.client.find_performer.return_value = None

        # Call _find_existing_performer
        performer = await mixin._find_existing_performer(test_account)

        # Verify performer is None
        assert performer is None
        mixin.context.client.find_performer.assert_called_once_with(
            test_account.username
        )

        # Case 4: find_performer returns a coroutine
        mixin.context.client.find_performer.reset_mock()

        # Create a coroutine that returns mock_performer
        async def mock_coroutine():
            return mock_performer

        mixin.context.client.find_performer.return_value = mock_coroutine()

        # Call _find_existing_performer
        performer = await mixin._find_existing_performer(test_account)

        # Verify performer
        assert performer == mock_performer

    @pytest.mark.asyncio
    async def test_update_account_stash_id(self, mixin, test_account, mock_performer):
        """Test _update_account_stash_id method."""
        # Create mock session
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.execute.return_value.scalar_one = AsyncMock(
            return_value=test_account
        )
        mock_session.flush = AsyncMock()

        # Call _update_account_stash_id
        await mixin._update_account_stash_id(
            test_account, mock_performer, session=mock_session
        )

        # Verify session operations
        mock_session.execute.assert_called_once()
        assert "12345" in str(mock_session.execute.call_args)
        assert test_account.stash_id == mock_performer.id
        mock_session.add.assert_called_once_with(test_account)
        mock_session.flush.assert_called_once()
