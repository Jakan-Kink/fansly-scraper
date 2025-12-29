"""Unit tests for AccountProcessingMixin.

These tests use respx_stash_processor fixture for edge mocking.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
import strawberry
from PIL import Image

# Re-query account to ensure relationship is properly loaded
from sqlalchemy import select

from metadata import Account
from tests.fixtures.metadata.metadata_factories import AccountFactory, MediaFactory
from tests.fixtures.stash.stash_graphql_fixtures import create_graphql_response
from tests.fixtures.stash.stash_type_factories import ImageFactory, PerformerFactory


class TestAccountProcessingMixin:
    """Test the account processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_account(self, respx_stash_processor, session):
        """Test _find_account method.

        This test doesn't require GraphQL mocking since it only tests database queries.
        """
        # Create test account in database
        account = AccountFactory.build(id=12345, username="test_user", stash_id=12345)
        session.add(account)
        await session.commit()

        # Call _find_account with creator_id
        await respx_stash_processor.context.get_client()
        found_account = await respx_stash_processor._find_account(session=session)

        # Verify account was found
        assert found_account is not None
        assert found_account.id == 12345
        assert found_account.username == "test_user"

        # Test with creator_name instead of id
        respx_stash_processor.state.creator_id = None

        # Call _find_account again
        found_account = await respx_stash_processor._find_account(session=session)

        # Verify account was found by username
        assert found_account is not None
        assert found_account.username == "test_user"

        # Test with no account found
        respx_stash_processor.state.creator_name = "nonexistent_user"

        # Call _find_account
        with patch(
            "stash.processing.mixins.account.print_warning"
        ) as mock_print_warning:
            found_account = await respx_stash_processor._find_account(session=session)

        # Verify no account and warning was printed
        assert found_account is None
        mock_print_warning.assert_called_once()
        assert "nonexistent_user" in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_process_creator(self, respx_stash_processor, session):
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

        # Setup edge mock for get_or_create_performer flow:
        # 1. findPerformers (fuzzy search) returns empty
        # 2. performerCreate creates new performer
        await respx_stash_processor.context.get_client()

        mock_performer = PerformerFactory.build(
            id="123",
            name="test_user",
            urls=["https://fansly.com/test_user"],
        )
        performer_dict = strawberry.asdict(mock_performer)

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers (fuzzy search) - no existing performer
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # performerCreate - create new performer
                httpx.Response(
                    200,
                    json=create_graphql_response("performerCreate", performer_dict),
                ),
            ]
        )

        # Call process_creator
        result_account, performer = await respx_stash_processor.process_creator(
            session=session
        )

        # Verify results
        assert result_account.id == account.id
        assert performer.id == "123"
        assert performer.name == "test_user"

        # Verify GraphQL calls were made
        assert graphql_route.call_count == 2  # findPerformers + performerCreate

        # Test with no account found
        respx_stash_processor.state.creator_id = (
            None  # Clear creator_id to force username lookup
        )
        respx_stash_processor.state.creator_name = "nonexistent"

        # Call process_creator and expect error
        with pytest.raises(ValueError) as excinfo:
            await respx_stash_processor.process_creator(session=session)

        # Verify error message
        assert "No account found for creator" in str(excinfo.value)
        assert "nonexistent" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_update_performer_avatar(self, respx_stash_processor, session):
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
        await respx_stash_processor.context.get_client()

        mock_performer = PerformerFactory.build(
            id="123",
            name="test_user",
        )

        await respx_stash_processor._update_performer_avatar(account, mock_performer)

        # Verify no GraphQL calls (no avatar to update)
        assert len(respx.calls) == 0

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

        # Create a temporary 2x2 red image file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
            temp_avatar_path = Path(tmp_file.name)
            img = Image.new("RGB", (2, 2), color="red")
            img.save(temp_avatar_path, "JPEG")

        try:
            # Mock GraphQL responses for avatar update
            # Create findImages response with image that has visual_files
            mock_image = ImageFactory.build(id="img_123")
            image_dict = strawberry.asdict(mock_image)
            # Add visual_files to the dict with all required ImageFile fields
            image_dict["visual_files"] = [
                {
                    "id": "file_123",
                    "path": str(temp_avatar_path),  # Use the actual temp file path
                    "basename": "avatar.jpg",
                    "parent_folder_id": "folder_123",
                    "mod_time": "2024-01-01T00:00:00Z",
                    "size": 1024,
                    "fingerprints": [],
                    "width": 100,
                    "height": 100,
                }
            ]

            images_response = {
                "count": 1,
                "images": [image_dict],
                "megapixels": 0.0,
                "filesize": 0.0,
            }

            performer_dict = strawberry.asdict(mock_performer)

            graphql_route = respx.post("http://localhost:9999/graphql").mock(
                side_effect=[
                    # findImages - find avatar image
                    httpx.Response(
                        200,
                        json=create_graphql_response("findImages", images_response),
                    ),
                    # performerUpdate - update avatar
                    httpx.Response(
                        200,
                        json=create_graphql_response("performerUpdate", performer_dict),
                    ),
                ]
            )

            # Call _update_performer_avatar with session
            await respx_stash_processor._update_performer_avatar(
                account, mock_performer, session=session
            )

            # Verify GraphQL calls were made
            assert graphql_route.call_count == 2  # findImages + performerUpdate
        finally:
            # Clean up temp file
            temp_avatar_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_id(self, respx_stash_processor, session):
        """Test _find_existing_performer finds performer by stash_id."""
        # Create account with stash_id (integer)
        account = AccountFactory.build(
            id=12345,
            username="test_user",
            stash_id=999,
        )
        session.add(account)
        await session.commit()

        # Setup context.client
        await respx_stash_processor.context.get_client()

        mock_performer = PerformerFactory.build(id="999", name="test_user")
        performer_dict = strawberry.asdict(mock_performer)

        # Mock GraphQL response - findPerformer by ID (singular)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformer", performer_dict),
                ),
            ]
        )

        performer = await respx_stash_processor._find_existing_performer(account)

        # Verify performer was found
        assert performer.id == "999"
        assert performer.name == "test_user"
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_name(
        self, respx_stash_processor, session
    ):
        """Test _find_existing_performer finds performer by username."""
        # Create account without stash_id
        account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        account.stash_id = None
        session.add(account)
        await session.commit()

        # Setup context.client
        await respx_stash_processor.context.get_client()

        mock_performer = PerformerFactory.build(id="999", name="test_user")
        performer_dict = strawberry.asdict(mock_performer)

        # Mock GraphQL response - findPerformers (plural) by name
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 1, "performers": [performer_dict]}
                    ),
                ),
            ]
        )

        performer = await respx_stash_processor._find_existing_performer(account)

        # Verify performer was found by username
        assert performer.id == "999"
        assert performer.name == "test_user"
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_find_existing_performer_not_found(
        self, respx_stash_processor, session
    ):
        """Test _find_existing_performer returns None when not found."""
        # Create account without stash_id
        account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        account.stash_id = None
        session.add(account)
        await session.commit()

        # Setup context.client
        await respx_stash_processor.context.get_client()

        # Mock GraphQL responses - not found by name or alias
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers by name - not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers by alias - not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
            ]
        )

        performer = await respx_stash_processor._find_existing_performer(account)

        # Verify performer is None
        assert performer is None
        assert graphql_route.call_count == 2  # name + alias

    @pytest.mark.asyncio
    async def test_update_account_stash_id(self, respx_stash_processor, session):
        """Test _update_account_stash_id method.

        This test doesn't require GraphQL mocking since it only updates the database.
        """
        # Create account
        account = AccountFactory.build(
            id=12345,
            username="test_user",
            stash_id=12345,
        )
        account.stash_id = None
        session.add(account)
        await session.commit()

        # Create mock performer
        mock_performer = PerformerFactory.build(id="123", name="test_user")

        # Call _update_account_stash_id
        await respx_stash_processor.context.get_client()
        await respx_stash_processor._update_account_stash_id(
            account, mock_performer, session=session
        )

        # Verify stash_id was updated (performer.id is string "123", converted to int)
        await session.refresh(account)
        assert account.stash_id == int(mock_performer.id)
