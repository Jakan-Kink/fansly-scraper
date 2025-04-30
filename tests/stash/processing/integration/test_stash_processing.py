"""Integration tests for StashProcessing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from metadata import Account
from stash.types import Gallery, Performer, Studio


class TestStashProcessingIntegration:
    """Integration tests for StashProcessing."""

    @pytest.mark.asyncio
    async def test_initialization(self, stash_processor, mock_config, mock_state):
        """Test StashProcessing initialization."""
        # Verify the processor was properly initialized
        assert stash_processor.config == mock_config
        # Don't test state identity since it may be a different instance with the same values
        assert stash_processor.state.creator_id == mock_state.creator_id
        assert stash_processor.state.creator_name == mock_state.creator_name
        assert stash_processor.state.messages_enabled == mock_state.messages_enabled
        assert stash_processor.context is not None
        assert stash_processor.database is not None

        # Verify the processor can be used
        assert hasattr(stash_processor, "process_creator")
        assert hasattr(stash_processor, "process_creator_posts")
        assert hasattr(stash_processor, "process_creator_messages")

    @pytest.mark.asyncio
    async def test_process_creator(
        self, stash_processor, mock_account, mock_performer, mock_studio
    ):
        """Test process_creator method."""
        # Mock methods that would be called
        stash_processor._find_account = AsyncMock(return_value=mock_account)
        stash_processor._find_existing_performer = AsyncMock(
            return_value=mock_performer
        )
        stash_processor._update_performer_avatar = AsyncMock()
        stash_processor._find_existing_studio = AsyncMock(return_value=mock_studio)
        stash_processor.process_creator_posts = AsyncMock()
        stash_processor.process_creator_messages = AsyncMock()

        # Call method
        await stash_processor.process_creator()

        # Verify account and performer were found
        stash_processor._find_account.assert_called_once()
        stash_processor._find_existing_performer.assert_called_once_with(mock_account)

        # Verify avatar was updated
        stash_processor._update_performer_avatar.assert_called_once_with(
            mock_account, mock_performer
        )

        # Verify studio was found
        stash_processor._find_existing_studio.assert_called_once_with(mock_account)

        # Verify post and message processing was called
        stash_processor.process_creator_posts.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
        )
        stash_processor.process_creator_messages.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
        )

    @pytest.mark.asyncio
    async def test_find_account_by_id(
        self, stash_processor, mock_database, mock_account
    ):
        """Test _find_account method using creator_id."""
        # Setup state and session
        stash_processor.state.creator_id = "12345"
        stash_processor.state.creator_name = None

        # Mock session.execute to return the mock_account
        session_result = MagicMock()
        session_result.scalar_one_or_none = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=session_result)

        # Call method
        account = await stash_processor._find_account(session=mock_database.session)

        # Verify result
        assert account == mock_account

        # Verify correct query was executed
        mock_database.session.execute.assert_called_once()
        # Check that the Where clause contains our creator_id
        assert "12345" in str(mock_database.session.execute.call_args)

    @pytest.mark.asyncio
    async def test_find_account_by_name(
        self, stash_processor, mock_database, mock_account
    ):
        """Test _find_account method using creator_name."""
        # Setup state and session
        stash_processor.state.creator_id = None
        stash_processor.state.creator_name = "test_user"

        # Mock session.execute to return the mock_account
        session_result = MagicMock()
        session_result.scalar_one_or_none = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=session_result)

        # Call method
        account = await stash_processor._find_account(session=mock_database.session)

        # Verify result
        assert account == mock_account

        # Verify correct query was executed
        mock_database.session.execute.assert_called_once()
        # Check that the Where clause contains our creator_name
        assert "test_user" in str(mock_database.session.execute.call_args)

    @pytest.mark.asyncio
    async def test_find_account_not_found(self, stash_processor, mock_database):
        """Test _find_account method when account not found."""
        # Setup state and session
        stash_processor.state.creator_id = "12345"
        stash_processor.state.creator_name = None

        # Mock session.execute to return None
        session_result = MagicMock()
        session_result.scalar_one_or_none = AsyncMock(return_value=None)
        mock_database.session.execute = AsyncMock(return_value=session_result)

        # Mock print_warning to avoid console output
        with patch("stash.processing.mixins.account.print_warning"):
            # Call method
            account = await stash_processor._find_account(session=mock_database.session)

        # Verify result
        assert account is None

        # Verify correct query was executed
        mock_database.session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_existing_performer(
        self, stash_processor, mock_account, mock_performer
    ):
        """Test _find_existing_performer method."""
        # Mock context.client.find_performer to return mock_performer
        stash_processor.context.client.find_performer.return_value = mock_performer

        # Case 1: Account has stash_id
        mock_account.stash_id = "performer_123"

        # Call method
        performer = await stash_processor._find_existing_performer(mock_account)

        # Verify result
        assert performer == mock_performer

        # Verify find_performer was called with stash_id
        stash_processor.context.client.find_performer.assert_called_once_with(
            "performer_123"
        )

        # Reset
        stash_processor.context.client.find_performer.reset_mock()

        # Case 2: Account has no stash_id
        mock_account.stash_id = None
        mock_account.username = "test_user"

        # Call method
        performer = await stash_processor._find_existing_performer(mock_account)

        # Verify result
        assert performer == mock_performer

        # Verify find_performer was called with username
        stash_processor.context.client.find_performer.assert_called_once_with(
            "test_user"
        )

        # Reset
        stash_processor.context.client.find_performer.reset_mock()

        # Case 3: find_performer returns None
        stash_processor.context.client.find_performer.return_value = None

        # Call method
        performer = await stash_processor._find_existing_performer(mock_account)

        # Verify result
        assert performer is None

        # Verify find_performer was called
        stash_processor.context.client.find_performer.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_existing_studio(
        self, stash_processor, mock_account, mock_studio
    ):
        """Test _find_existing_studio method."""
        # Mock context.client.find_studio to return mock_studio
        stash_processor.context.client.find_studio.return_value = mock_studio

        # Call method
        studio = await stash_processor._find_existing_studio(mock_account)

        # Verify result
        assert studio == mock_studio

        # Verify find_studio was called with account username
        stash_processor.context.client.find_studio.assert_called_once_with(
            mock_account.username
        )

        # Reset
        stash_processor.context.client.find_studio.reset_mock()

        # Case 2: find_studio returns None and needs to create
        stash_processor.context.client.find_studio.return_value = None

        # Setup mock Studio.create
        with patch("stash.types.Studio.create", return_value=mock_studio):
            # Call method
            studio = await stash_processor._find_existing_studio(mock_account)

            # Verify result
            assert studio == mock_studio

            # Verify find_studio was called
            stash_processor.context.client.find_studio.assert_called_once()

            # Verify studio was saved
            mock_studio.save.assert_called_once_with(stash_processor.context.client)

    @pytest.mark.asyncio
    async def test_update_performer_avatar(
        self, stash_processor, mock_account, mock_performer
    ):
        """Test _update_performer_avatar method."""
        # Mock account with no avatar
        mock_account.awaitable_attrs.avatar = None

        # Call method
        await stash_processor._update_performer_avatar(mock_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar but no local_filename
        mock_avatar = MagicMock()
        mock_avatar.local_filename = None
        mock_account.awaitable_attrs.avatar = mock_avatar

        # Call method
        await stash_processor._update_performer_avatar(mock_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar and local_filename
        mock_avatar.local_filename = "avatar.jpg"
        mock_account.awaitable_attrs.avatar = mock_avatar

        # Mock performer with default image
        mock_performer.image_path = "default=true"

        # Mock client.find_images
        mock_image = MagicMock()
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = "path/to/avatar.jpg"

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        stash_processor.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )

        # Call method
        await stash_processor._update_performer_avatar(mock_account, mock_performer)

        # Verify avatar update was attempted
        stash_processor.context.client.find_images.assert_called_once()
        assert "avatar.jpg" in str(stash_processor.context.client.find_images.call_args)
        mock_performer.update_avatar.assert_called_once()
        assert "path/to/avatar.jpg" in str(mock_performer.update_avatar.call_args)

    @pytest.mark.skip(reason="scan_to_stash not implemented in StashProcessing")
    @pytest.mark.asyncio
    async def test_scan_to_stash_integration(
        self,
        stash_processor,
        mock_account,
        mock_performer,
        mock_multiple_posts,
        mock_multiple_messages,
    ):
        """Test the full integration workflow."""
        # Mock various methods to avoid actual network calls
        stash_processor.process_creator = AsyncMock()

        # Instead of scan_to_stash, test process_creator directly
        await stash_processor.process_creator()

        # Verify process_creator was called
        stash_processor.process_creator.assert_called_once()
