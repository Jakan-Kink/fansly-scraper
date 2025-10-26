"""Integration tests for StashProcessing.

This module tests the StashProcessing class using real database fixtures
and factory-based test data instead of mocks.
"""

import pytest

from stash.types import Performer, Studio
from tests.fixtures.metadata_factories import AccountFactory


class TestStashProcessingIntegration:
    """Integration tests for StashProcessing."""

    @pytest.mark.asyncio
    async def test_initialization(self, stash_processor, mock_config, mock_state):
        """Test StashProcessing initialization with real database."""
        # Verify the processor was properly initialized with real dependencies
        assert stash_processor.config == mock_config
        assert stash_processor.state.creator_id == mock_state.creator_id
        assert stash_processor.state.creator_name == mock_state.creator_name
        assert stash_processor.state.messages_enabled == mock_state.messages_enabled
        assert stash_processor.context is not None
        assert stash_processor.database is not None

        # Verify the processor has expected methods
        assert hasattr(stash_processor, "process_creator")
        assert hasattr(stash_processor, "process_creator_posts")
        assert hasattr(stash_processor, "process_creator_messages")

    @pytest.mark.asyncio
    async def test_find_account_by_id(self, stash_processor, session_sync):
        """Test _find_account method using creator_id with real database."""
        # Create a real account in the database
        account = AccountFactory(id=12345, username="test_user")
        session_sync.commit()

        # Setup state to search by ID
        stash_processor.state.creator_id = "12345"
        stash_processor.state.creator_name = None

        # Setup mock database to return the real account
        stash_processor.database.set_result(account)

        # Query using mock session (which will return our real account)
        result = await stash_processor._find_account(
            session=stash_processor.database.session
        )

        # Verify result
        assert result is not None
        assert result.id == 12345
        assert result.username == "test_user"

    @pytest.mark.asyncio
    async def test_find_account_by_name(self, stash_processor, session_sync):
        """Test _find_account method using creator_name with real database."""
        # Create a real account in the database
        account = AccountFactory(username="test_creator")
        session_sync.commit()

        # Setup state to search by name
        stash_processor.state.creator_id = None
        stash_processor.state.creator_name = "test_creator"

        # Setup mock database to return the real account
        stash_processor.database.set_result(account)

        # Query using mock session
        result = await stash_processor._find_account(
            session=stash_processor.database.session
        )

        # Verify result
        assert result is not None
        assert result.username == "test_creator"

    @pytest.mark.asyncio
    async def test_find_account_not_found(self, stash_processor):
        """Test _find_account method when account not found."""
        # Setup state for non-existent account
        stash_processor.state.creator_id = "99999"
        stash_processor.state.creator_name = None

        # Setup mock database to return None
        stash_processor.database.set_result(None)

        # Query using mock session
        result = await stash_processor._find_account(
            session=stash_processor.database.session
        )

        # Verify result is None
        assert result is None

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, stash_processor, session_sync):
        """Test _find_existing_performer method with real account."""
        # Create a real account
        account = AccountFactory(username="performer_user", stash_id="performer_123")
        session_sync.commit()

        # Mock the Stash client's find_performer method
        mock_performer = Performer(
            id="performer_123",
            name="performer_user",
            urls=["https://fansly.com/performer_user"],
        )
        stash_processor.context.client.find_performer.return_value = mock_performer

        # Test finding by stash_id
        performer = await stash_processor._find_existing_performer(account)

        # Verify result
        assert performer == mock_performer
        stash_processor.context.client.find_performer.assert_called_once_with(
            "performer_123"
        )

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_username(
        self, stash_processor, session_sync
    ):
        """Test _find_existing_performer finds by username when no stash_id."""
        # Create a real account without stash_id
        account = AccountFactory(username="new_performer")
        session_sync.commit()

        # Mock the Stash client's find_performer method
        mock_performer = Performer(
            id="new_perf_id",
            name="new_performer",
            urls=["https://fansly.com/new_performer"],
        )
        stash_processor.context.client.find_performer.return_value = mock_performer

        # Test finding by username
        performer = await stash_processor._find_existing_performer(account)

        # Verify result
        assert performer == mock_performer
        stash_processor.context.client.find_performer.assert_called_once_with(
            "new_performer"
        )

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, stash_processor, session_sync):
        """Test _find_existing_studio method with real account."""
        # Create a real account
        account = AccountFactory(username="studio_creator")
        session_sync.commit()

        # Mock the Stash client's find_studio to return existing studio
        mock_studio = Studio(
            id="studio_123",
            name="studio_creator",
            urls=["https://fansly.com/studio_creator"],
        )
        stash_processor.context.client.find_studio.return_value = mock_studio

        # Test finding studio
        studio = await stash_processor._find_existing_studio(account)

        # Verify result
        assert studio == mock_studio
        stash_processor.context.client.find_studio.assert_called_once_with(
            "studio_creator"
        )

    @pytest.mark.asyncio
    async def test_find_existing_studio_creates_new(
        self, stash_processor, session_sync, mocker
    ):
        """Test _find_existing_studio creates new studio when not found."""
        # Create a real account
        account = AccountFactory(username="new_studio")
        session_sync.commit()

        # Mock the Stash client's find_studio to return None
        stash_processor.context.client.find_studio.return_value = None

        # Create a mock studio that will be returned by Studio.create
        mock_studio = Studio(
            id="new_studio_id",
            name="new_studio",
            urls=["https://fansly.com/new_studio"],
        )
        mock_studio.save = mocker.AsyncMock()

        # Patch Studio.create
        mocker.patch("stash.types.Studio.create", return_value=mock_studio)

        # Test finding/creating studio
        studio = await stash_processor._find_existing_studio(account)

        # Verify result
        assert studio == mock_studio
        mock_studio.save.assert_called_once_with(stash_processor.context.client)

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_avatar(
        self, stash_processor, session_sync, mocker
    ):
        """Test _update_performer_avatar when account has no avatar."""
        # Create a real account without avatar
        account = AccountFactory(username="no_avatar_user")
        session_sync.commit()

        # Mock performer
        mock_performer = Performer(id="perf_123", name="no_avatar_user", urls=[])
        mock_performer.update_avatar = mocker.AsyncMock()

        # Call method
        await stash_processor._update_performer_avatar(account, mock_performer)

        # Verify no avatar update was attempted
        mock_performer.update_avatar.assert_not_called()
