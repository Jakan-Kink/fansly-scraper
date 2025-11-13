"""Integration tests for StashProcessing.

This module tests the StashProcessing class using real database fixtures
and factory-based test data instead of mocks.
"""

import pytest

from stash.types import Performer
from tests.fixtures.metadata.metadata_factories import AccountFactory


class TestStashProcessingIntegration:
    """Integration tests for StashProcessing."""

    @pytest.mark.asyncio
    async def test_initialization(self, factory_session, real_stash_processor, mock_state):
        """Test StashProcessing initialization with real database."""
        # Verify the processor was properly initialized with real dependencies
        assert real_stash_processor.config is not None
        assert real_stash_processor.state.creator_id == mock_state.creator_id
        assert real_stash_processor.state.creator_name == mock_state.creator_name
        assert real_stash_processor.state.messages_enabled == mock_state.messages_enabled
        assert real_stash_processor.context is not None
        assert real_stash_processor.database is not None

        # Verify the processor has expected methods
        assert hasattr(real_stash_processor, "process_creator")
        assert hasattr(real_stash_processor, "process_creator_posts")
        assert hasattr(real_stash_processor, "process_creator_messages")

    @pytest.mark.asyncio
    async def test_find_account_by_id(
        self, factory_session, real_stash_processor, test_database_sync
    ):
        """Test _find_account method using creator_id with real database."""
        # Create a real account in the database
        account = AccountFactory(id=12345, username="test_user")
        factory_session.commit()

        # Setup state to search by ID
        real_stash_processor.state.creator_id = "12345"
        real_stash_processor.state.creator_name = None

        # Use real async session from database
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result
        assert result is not None
        assert result.id == 12345
        assert result.username == "test_user"

    @pytest.mark.asyncio
    async def test_find_account_by_name(
        self, factory_session, real_stash_processor, test_database_sync
    ):
        """Test _find_account method using creator_name with real database."""
        # Create a real account in the database
        account = AccountFactory(username="test_creator")
        factory_session.commit()

        # Setup state to search by name
        real_stash_processor.state.creator_id = None
        real_stash_processor.state.creator_name = "test_creator"

        # Use real async session from database
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result
        assert result is not None
        assert result.username == "test_creator"

    @pytest.mark.asyncio
    async def test_find_account_not_found(
        self, factory_session, real_stash_processor, test_database_sync
    ):
        """Test _find_account method when account not found."""
        # Setup state for non-existent account
        real_stash_processor.state.creator_id = "99999"
        real_stash_processor.state.creator_name = None

        # Use real async session - no account exists so will return None
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result is None
        assert result is None

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, factory_session, real_stash_processor):
        """Test _find_existing_performer method with real account."""
        # Create a real account with integer stash_id
        account = AccountFactory(username="performer_user", stash_id=123)
        factory_session.commit()

        # Mock the Stash client's find_performer method
        mock_performer = Performer(
            id="123",
            name="performer_user",
            urls=["https://fansly.com/performer_user"],
        )
        real_stash_processor.context.client.find_performer.return_value = mock_performer

        # Test finding by stash_id (converted to string for Stash API)
        performer = await real_stash_processor._find_existing_performer(account)

        # Verify result
        assert performer == mock_performer
        real_stash_processor.context.client.find_performer.assert_called_once_with(
            123  # Account.stash_id is integer from database; find_performer accepts int
        )

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_username(
        self, factory_session, real_stash_processor
    ):
        """Test _find_existing_performer finds by username when no stash_id."""
        # Create a real account without stash_id
        account = AccountFactory(username="new_performer")
        factory_session.commit()

        # Mock the Stash client's find_performer method
        mock_performer = Performer(
            id="new_perf_id",
            name="new_performer",
            urls=["https://fansly.com/new_performer"],
        )
        real_stash_processor.context.client.find_performer.return_value = mock_performer

        # Test finding by username
        performer = await real_stash_processor._find_existing_performer(account)

        # Verify result
        assert performer == mock_performer
        real_stash_processor.context.client.find_performer.assert_called_once_with(
            "new_performer"
        )

    @pytest.mark.asyncio
    async def test_find_existing_studio(
        self, factory_session, real_stash_processor, fansly_network_studio
    ):
        """Test _find_existing_studio method with real account when studio exists."""
        from unittest.mock import AsyncMock

        import strawberry

        from stash.types import FindStudiosResultType
        from tests.fixtures.stash.stash_type_factories import StudioFactory

        # Create a real account using factory
        account = AccountFactory(username="studio_creator")
        factory_session.commit()

        # Create expected creator studio using factory
        creator_studio = StudioFactory(
            id="studio_123",
            name=f"{account.username} (Fansly)",
            url=f"https://fansly.com/{account.username}",
            parent_studio=fansly_network_studio,
        )

        # Mock find_studios to return dicts (matching real GraphQL behavior)
        async def mock_find_studios(q=None, **kwargs):
            if q == "Fansly (network)":
                return FindStudiosResultType(
                    count=1, studios=[strawberry.asdict(fansly_network_studio)]
                )
            if q == f"{account.username} (Fansly)":
                # Creator studio already exists
                return FindStudiosResultType(
                    count=1, studios=[strawberry.asdict(creator_studio)]
                )
            return FindStudiosResultType(count=0, studios=[])

        real_stash_processor.context.client.find_studios = AsyncMock(
            side_effect=mock_find_studios
        )

        # Test finding existing studio
        studio = await real_stash_processor._find_existing_studio(account)

        # Verify result - should return existing creator studio
        assert studio is not None
        assert studio.name == f"{account.username} (Fansly)"

    @pytest.mark.asyncio
    async def test_find_existing_studio_creates_new(
        self, factory_session, real_stash_processor, fansly_network_studio
    ):
        """Test _find_existing_studio creates new studio when not found."""
        from unittest.mock import AsyncMock

        from stash.types import FindStudiosResultType
        from tests.fixtures.stash.stash_type_factories import StudioFactory

        # Create a real account
        account = AccountFactory(username="new_studio_creator")
        factory_session.commit()

        # Create expected creator studio using factory
        expected_studio = StudioFactory(
            id="999",
            name=f"{account.username} (Fansly)",
            url=f"https://fansly.com/{account.username}",
            parent_studio=fansly_network_studio,
        )

        # Mock find_studios to return dicts (matching real GraphQL behavior)
        import strawberry

        async def mock_find_studios(q=None, **kwargs):
            if q == "Fansly (network)":
                return FindStudiosResultType(
                    count=1, studios=[strawberry.asdict(fansly_network_studio)]
                )
            # Creator studio not found, needs to be created
            return FindStudiosResultType(count=0, studios=[])

        real_stash_processor.context.client.find_studios = AsyncMock(
            side_effect=mock_find_studios
        )
        real_stash_processor.context.client.create_studio = AsyncMock(
            return_value=expected_studio
        )
        real_stash_processor.context.client.find_performer = AsyncMock(return_value=None)

        # Test finding/creating studio
        studio = await real_stash_processor._find_existing_studio(account)

        # Verify studio was created with correct properties
        assert studio is not None
        assert studio.name == f"{account.username} (Fansly)"
        assert studio.parent_studio == fansly_network_studio
        # Verify create_studio was called
        real_stash_processor.context.client.create_studio.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_avatar(
        self, factory_session, real_stash_processor, mocker
    ):
        """Test _update_performer_avatar when account has no avatar."""
        # Create a real account without avatar
        account = AccountFactory(username="no_avatar_user")
        factory_session.commit()

        # Mock performer
        mock_performer = Performer(id="perf_123", name="no_avatar_user", urls=[])
        mock_performer.update_avatar = mocker.AsyncMock()

        # Call method
        await real_stash_processor._update_performer_avatar(account, mock_performer)

        # Verify no avatar update was attempted
        mock_performer.update_avatar.assert_not_called()
