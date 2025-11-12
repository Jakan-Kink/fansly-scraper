"""Unit tests for AccountProcessingMixin.

These tests use the shared account_mixin fixture from tests/fixtures/stash_mixin_fixtures.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

# Re-query account to ensure relationship is properly loaded
from sqlalchemy import select

from metadata import Account
from tests.fixtures.metadata_factories import AccountFactory, MediaFactory


class TestAccountProcessingMixin:
    """Test the account processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_account(self, account_mixin, session):
        """Test _find_account method."""
        # Create test account in database
        account = AccountFactory.build(id=12345, username="test_user", stash_id=12345)
        session.add(account)
        await session.commit()

        # Call _find_account with creator_id
        found_account = await account_mixin._find_account(session=session)

        # Verify account was found
        assert found_account is not None
        assert found_account.id == 12345
        assert found_account.username == "test_user"

        # Test with creator_name instead of id
        account_mixin.state.creator_id = None

        # Call _find_account again
        found_account = await account_mixin._find_account(session=session)

        # Verify account was found by username
        assert found_account is not None
        assert found_account.username == "test_user"

        # Test with no account found
        account_mixin.state.creator_name = "nonexistent_user"

        # Call _find_account
        with patch(
            "stash.processing.mixins.account.print_warning"
        ) as mock_print_warning:
            found_account = await account_mixin._find_account(session=session)

        # Verify no account and warning was printed
        assert found_account is None
        mock_print_warning.assert_called_once()
        assert "nonexistent_user" in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_process_creator(self, account_mixin, session, mock_performer):
        """Test process_creator method."""
        # Create test account in database
        account = AccountFactory.build(
            id=12345,
            username="test_user",
            stash_id=12345,
        )
        account.stash_id = None
        session.add(account)
        await session.commit()

        # Setup context.client methods
        account_mixin.context.client.find_performer = AsyncMock(
            return_value=mock_performer
        )
        account_mixin.context.client.get_or_create_performer = AsyncMock(
            return_value=mock_performer
        )

        # Call process_creator
        result_account, performer = await account_mixin.process_creator(session=session)

        # Verify results
        assert result_account.id == account.id
        assert performer == mock_performer
        # The implementation calls get_or_create_performer, not find_performer directly
        account_mixin.context.client.get_or_create_performer.assert_called_once()

        # Test with no existing performer (creates new one)
        account_mixin.context.client.find_performer.reset_mock()
        account_mixin.context.client.find_performer.return_value = None

        # Mock performer creation
        new_performer = mock_performer  # Use same fixture
        account_mixin.context.client.get_or_create_performer = AsyncMock(
            return_value=new_performer
        )

        # Call process_creator
        result_account, performer = await account_mixin.process_creator(session=session)

        # Verify get_or_create_performer was called
        # Note: get_or_create_performer handles the performer creation internally,
        # so we don't check Performer.from_account or save() calls
        account_mixin.context.client.get_or_create_performer.assert_called_once()

        # Test with no account found
        account_mixin.state.creator_id = (
            None  # Clear creator_id to force username lookup
        )
        account_mixin.state.creator_name = "nonexistent"

        # Call process_creator and expect error
        with pytest.raises(ValueError) as excinfo:  # noqa: PT011 - message validated by assertions below
            await account_mixin.process_creator(session=session)

        # Verify error message
        assert "No account found for creator" in str(excinfo.value)
        assert "nonexistent" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_update_performer_avatar(
        self, account_mixin, session, mock_performer
    ):
        """Test _update_performer_avatar method."""
        # Create account with no avatar
        account = AccountFactory.build(
            id=12345,
            username="test_user",
            stash_id=12345,
        )
        session.add(account)
        await session.commit()

        # Refresh to get awaitable_attrs
        await session.refresh(account)

        # Call _update_performer_avatar with no avatar
        await account_mixin._update_performer_avatar(account, mock_performer)

        # Verify no avatar update was attempted (mock_performer.update_avatar is a real method, not called)

        # Create account with avatar
        avatar = MediaFactory.build(
            id=1,
            accountId=account.id,  # Set foreign key to match account
            local_filename="avatar.jpg",
        )
        session.add(avatar)
        await session.commit()

        # Link avatar to account through association table
        from metadata import account_avatar

        await session.execute(
            account_avatar.insert().values(accountId=account.id, mediaId=avatar.id)
        )
        await session.commit()

        # Re-query account to get fresh instance
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Mock performer with default image
        mock_performer.image_path = "default=true"
        mock_performer.update_avatar = AsyncMock()

        # Mock client.find_images
        mock_image = type(
            "obj",
            (object,),
            {
                "visual_files": [
                    type("obj", (object,), {"path": "path/to/avatar.jpg"})()
                ]
            },
        )()
        mock_image_result = type(
            "obj", (object,), {"count": 1, "images": [mock_image]}
        )()

        account_mixin.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )

        # Call _update_performer_avatar with session
        await account_mixin._update_performer_avatar(
            account, mock_performer, session=session
        )

        # Verify avatar update was attempted
        account_mixin.context.client.find_images.assert_called_once()
        assert "avatar.jpg" in str(account_mixin.context.client.find_images.call_args)
        mock_performer.update_avatar.assert_called_once()
        assert "path/to/avatar.jpg" in str(mock_performer.update_avatar.call_args)

    @pytest.mark.asyncio
    async def test_find_existing_performer(
        self, account_mixin, session, mock_performer
    ):
        """Test _find_existing_performer method."""
        # Create account with stash_id (integer)
        account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        account.stash_id = 999  # Use integer, not string
        session.add(account)
        await session.commit()

        # Setup context.client.find_performer
        account_mixin.context.client.find_performer = AsyncMock(
            return_value=mock_performer
        )

        # Case 1: Account has stash_id
        performer = await account_mixin._find_existing_performer(account)

        # Verify performer and find_performer was called with stash_id (as integer)
        assert performer == mock_performer
        account_mixin.context.client.find_performer.assert_called_once_with(999)

        # Case 2: Account has no stash_id
        account_mixin.context.client.find_performer.reset_mock()
        account.stash_id = None

        # Call _find_existing_performer
        performer = await account_mixin._find_existing_performer(account)

        # Verify performer and find_performer was called with username
        assert performer == mock_performer
        account_mixin.context.client.find_performer.assert_called_once_with(
            account.username
        )

        # Case 3: find_performer returns None
        account_mixin.context.client.find_performer.reset_mock()
        account_mixin.context.client.find_performer.return_value = None

        # Call _find_existing_performer
        performer = await account_mixin._find_existing_performer(account)

        # Verify performer is None
        assert performer is None
        account_mixin.context.client.find_performer.assert_called_once_with(
            account.username
        )

    @pytest.mark.asyncio
    async def test_update_account_stash_id(
        self, account_mixin, session, mock_performer
    ):
        """Test _update_account_stash_id method."""
        # Create account
        account = AccountFactory.build(
            id=12345,
            username="test_user",
            stash_id=12345,
        )
        account.stash_id = None
        session.add(account)
        await session.commit()

        # Call _update_account_stash_id
        await account_mixin._update_account_stash_id(
            account, mock_performer, session=session
        )

        # Verify stash_id was updated (performer.id is string "123", converted to int)
        await session.refresh(account)
        assert account.stash_id == int(mock_performer.id)
