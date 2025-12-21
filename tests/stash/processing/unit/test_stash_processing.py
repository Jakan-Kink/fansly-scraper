"""Unit tests for stash processing module - core functionality.

Migrated to use respx_stash_processor fixture with proper edge-mocking:
- ✅ Use respx to mock HTTP responses at Stash GraphQL boundary
- ✅ Use real async database sessions (no mocking AsyncSession)
- ✅ Use real StashProcessing, StashClient, Database instances
- ❌ Do NOT mock internal class methods
"""

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from stash_graphql_client.client.utils import sanitize_model_data

from metadata import account_avatar
from tests.fixtures.metadata.metadata_factories import AccountFactory, MediaFactory
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_images_result,
    create_find_performers_result,
    create_graphql_response,
    create_image_dict,
    create_performer_dict,
)
from tests.fixtures.stash.stash_type_factories import ImageFileFactory, PerformerFactory


class TestStashProcessingAccount:
    """Test the account-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_account(self, respx_stash_processor, session):
        """Test _find_account method - UNIT TEST with real database, no Stash API.

        Verifies this is a pure database operation with NO HTTP calls.
        """
        # Create test account in real database
        test_account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        session.add(test_account)
        await session.commit()

        # Set processor state to match account
        respx_stash_processor.state.creator_id = "12345"

        # Call _find_account
        account = await respx_stash_processor._find_account(session=session)

        # Verify account was found via database query
        assert account is not None
        assert account.id == 12345
        assert account.username == "test_user"

        # Verify NO HTTP calls were made via respx routes
        assert len(respx.routes) == 1  # Only the default route exists
        assert not respx.routes[0].called, (
            "Database-only operation should not make HTTP calls"
        )

        # Test with no account found
        respx_stash_processor.state.creator_id = "99999"  # Non-existent ID

        # Call _find_account and verify warning
        with patch(
            "stash.processing.mixins.account.print_warning"
        ) as mock_print_warning:
            account = await respx_stash_processor._find_account(session=session)

        # Verify no account found and warning was printed
        assert account is None
        mock_print_warning.assert_called_once()
        assert respx_stash_processor.state.creator_name in str(
            mock_print_warning.call_args
        )

        # Verify NO HTTP calls were made
        assert not respx.routes[0].called, (
            "Database-only operation should not make HTTP calls"
        )

    @pytest.mark.asyncio
    async def test_update_account_stash_id(self, respx_stash_processor, session):
        """Test _update_account_stash_id method - UNIT TEST with real database, no Stash API.

        Verifies this is a pure database operation with NO HTTP calls.
        """
        # Create test account in real database
        test_account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        test_account.stash_id = None  # Start with no stash_id
        session.add(test_account)
        await session.commit()

        # Create test performer using factory
        test_performer = PerformerFactory(
            id="123",  # Use numeric string since code converts to int
            name="test_user",
        )

        # Call _update_account_stash_id
        await respx_stash_processor._update_account_stash_id(
            test_account, test_performer, session=session
        )

        # Verify account stash_id was updated via database
        await session.refresh(test_account)
        assert test_account.stash_id == int(test_performer.id)

        # Verify NO HTTP calls were made via respx routes
        assert len(respx.routes) == 1  # Only the default route exists
        assert not respx.routes[0].called, (
            "Database-only operation should not make HTTP calls"
        )


class TestStashProcessingPerformer:
    """Test the performer-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, respx_stash_processor):
        """Test _find_existing_performer method - UNIT TEST with respx HTTP mocking.

        Uses chained respx responses to test multiple cases in sequence.
        """
        # Create test performer data using helper
        performer_data = create_performer_dict(
            id="123",
            name="test_user",
        )
        performers_result = create_find_performers_result(
            count=1, performers=[performer_data]
        )

        # Clear the cache before testing
        if hasattr(respx_stash_processor._find_existing_performer, "cache_clear"):
            respx_stash_processor._find_existing_performer.cache_clear()

        # Create chained responses for 3 test cases
        responses = [
            # Case 1: Find by ID (account has stash_id) - uses findPerformer
            httpx.Response(
                200, json=create_graphql_response("findPerformer", performer_data)
            ),
            # Case 2: Find by username (no stash_id) - uses findPerformers
            httpx.Response(
                200, json=create_graphql_response("findPerformers", performers_result)
            ),
            # Case 3: Not found (returns empty result)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findPerformers",
                    create_find_performers_result(count=0, performers=[]),
                ),
            ),
        ]

        # Set up route with chained responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=responses
        )

        # Case 1: Account has stash_id - search by ID (uses findPerformer query)
        test_account_1 = AccountFactory.build(username="test_user")
        test_account_1.stash_id = "123"

        performer = await respx_stash_processor._find_existing_performer(test_account_1)

        # Verify performer was found
        assert performer is not None
        assert performer.id == "123"
        assert performer.name == "test_user"

        # Inspect the first HTTP request
        assert len(graphql_route.calls) == 1
        request_body = json.loads(graphql_route.calls[0].request.content)
        assert "findPerformer" in request_body["query"]
        assert request_body["variables"]["id"] == "123"

        # Case 2: Account has no stash_id - search by username (uses findPerformers query)
        test_account_2 = AccountFactory.build(username="test_user_2")
        test_account_2.stash_id = None

        performer = await respx_stash_processor._find_existing_performer(test_account_2)

        # Verify performer was found
        assert performer is not None
        assert performer.id == "123"

        # Inspect the second HTTP request
        assert len(graphql_route.calls) == 2
        request_body = json.loads(graphql_route.calls[1].request.content)
        assert (
            "findPerformers" in request_body["query"]
        )  # Note: plural when searching by name

        # Case 3: Performer not found - GraphQL returns empty result
        test_account_3 = AccountFactory.build(username="nonexistent_user")
        test_account_3.stash_id = None

        performer = await respx_stash_processor._find_existing_performer(test_account_3)

        # Verify no performer found
        assert performer is None

        # Inspect the third HTTP request
        assert len(graphql_route.calls) == 3
        request_body = json.loads(graphql_route.calls[2].request.content)
        assert (
            "findPerformers" in request_body["query"]
        )  # Note: plural when searching by name

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_avatar(
        self, respx_stash_processor, session
    ):
        """Test _update_performer_avatar with account that has no avatar.

        Verifies NO HTTP calls are made when account has no avatar.
        """
        # Create test performer - use REAL performer from factory
        test_performer = PerformerFactory(
            id="123",
            name="test_user",
            image_path="default=true",
        )

        # Create account with no avatar
        test_account = AccountFactory.build(
            id=12345,
            username="test_user",
        )
        session.add(test_account)
        await session.commit()

        await respx_stash_processor._update_performer_avatar(
            test_account, test_performer, session=session
        )

        # Verify NO HTTP calls were made (returns early before HTTP)
        assert len(respx.routes) == 1  # Only the default route exists
        assert not respx.routes[0].called, "No avatar should not make HTTP calls"

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_local_filename(
        self, respx_stash_processor, session
    ):
        """Test _update_performer_avatar with avatar that has no local_filename.

        Verifies NO HTTP calls are made when avatar has no local_filename.
        """
        # Create test performer - use REAL performer
        test_performer = PerformerFactory(
            id="123",
            name="test_user",
            image_path="default=true",
        )

        # Create account with avatar but no local_filename
        test_account = AccountFactory.build(
            id=12346,
            username="test_user_2",
        )
        session.add(test_account)

        # Create avatar media with no local_filename
        avatar = MediaFactory.build(
            id=99998,
            accountId=12346,
            local_filename=None,  # No local file
        )
        session.add(avatar)
        await session.commit()

        # Link avatar to account via association table
        await session.execute(
            account_avatar.insert().values(accountId=12346, mediaId=99998)
        )
        await session.commit()

        await respx_stash_processor._update_performer_avatar(
            test_account, test_performer, session=session
        )

        # Verify NO HTTP calls were made (returns early)
        assert not respx.routes[0].called, (
            "No local_filename should not make HTTP calls"
        )

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_images_found(
        self, respx_stash_processor, session, tmp_path
    ):
        """Test _update_performer_avatar when no images found in Stash.

        Verifies findImages is called and returns early (no performerUpdate call).
        """
        # Create test performer - use REAL performer
        test_performer = PerformerFactory(
            id="123",
            name="test_user",
            image_path="default=true",
        )

        # Create account with avatar and local_filename
        test_account = AccountFactory.build(
            id=12348,
            username="test_user_4",
        )
        session.add(test_account)

        # Create avatar media with local_filename
        avatar = MediaFactory.build(
            id=99997,
            accountId=12348,
            local_filename="missing_avatar.jpg",
        )
        session.add(avatar)
        await session.commit()

        # Link avatar to account
        await session.execute(
            account_avatar.insert().values(accountId=12348, mediaId=99997)
        )
        await session.commit()

        # Create GraphQL response for findImages - empty result
        empty_images_response = create_find_images_result(count=0, images=[])

        # Mock findImages GraphQL response
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200, json=create_graphql_response("findImages", empty_images_response)
            )
        )

        await respx_stash_processor._update_performer_avatar(
            test_account, test_performer, session=session
        )

        # Verify findImages was called
        assert len(graphql_route.calls) == 1
        request_body = json.loads(graphql_route.calls[0].request.content)
        assert "findImages" in request_body["query"]

    @pytest.mark.asyncio
    async def test_update_performer_avatar_success(
        self, respx_stash_processor, session, tmp_path
    ):
        """Test _update_performer_avatar successfully updates avatar.

        Uses REAL performer.update_avatar with temp file and mocked HTTP responses.
        """
        # Create a temp image file for testing
        test_image = tmp_path / "avatar.jpg"
        test_image.write_bytes(b"fake image data")

        # Create test performer - use REAL performer
        test_performer = PerformerFactory(
            id="123",
            name="test_user",
            image_path="default=true",
        )

        # Create account with avatar and local_filename
        test_account = AccountFactory.build(
            id=12347,
            username="test_user_3",
        )
        session.add(test_account)

        # Create avatar media with local_filename pointing to temp file
        avatar = MediaFactory.build(
            id=99999,
            accountId=12347,
            local_filename=str(test_image),  # Use real temp file path
        )
        session.add(avatar)
        await session.commit()

        # Link avatar to account
        await session.execute(
            account_avatar.insert().values(accountId=12347, mediaId=99999)
        )
        await session.commit()

        # Create GraphQL responses for findImages and performerUpdate
        # Use factory to create ImageFile, then convert to dict with datetime as string
        image_file = ImageFileFactory(
            id="123",
            path=str(test_image),
            basename="avatar.jpg",
            size=len(test_image.read_bytes()),
            width=800,
            height=600,
        )
        image_file_dict = sanitize_model_data(image_file.__dict__)
        # Convert datetime to ISO string for JSON serialization
        if image_file_dict.get("mod_time"):
            image_file_dict["mod_time"] = image_file_dict["mod_time"].isoformat()
        image_data = create_image_dict(
            id="456",
            title="Avatar",
            visual_files=[image_file_dict],
        )
        images_response = create_find_images_result(count=1, images=[image_data])
        performer_response = create_performer_dict(id="123", name="test_user")

        # Mock both GraphQL responses with chained responses
        responses = [
            # findImages response
            httpx.Response(
                200, json=create_graphql_response("findImages", images_response)
            ),
            # performerUpdate response
            httpx.Response(
                200, json=create_graphql_response("performerUpdate", performer_response)
            ),
        ]

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=responses
        )

        await respx_stash_processor._update_performer_avatar(
            test_account, test_performer, session=session
        )

        # Verify both GraphQL calls were made
        assert len(graphql_route.calls) == 2

        # Verify first call was findImages
        request_body_1 = json.loads(graphql_route.calls[0].request.content)
        assert "findImages" in request_body_1["query"]
        assert str(test_image) in str(request_body_1["variables"])

        # Verify second call was performerUpdate
        request_body_2 = json.loads(graphql_route.calls[1].request.content)
        assert "performerUpdate" in request_body_2["query"]

    @pytest.mark.asyncio
    async def test_update_performer_avatar_exception(
        self, respx_stash_processor, session, tmp_path
    ):
        """Test _update_performer_avatar when file doesn't exist (triggers exception).

        Uses non-existent file path to trigger FileNotFoundError in real update_avatar.
        """
        # Create test performer - use REAL performer
        test_performer = PerformerFactory(
            id="123",
            name="test_user",
            image_path="default=true",
        )

        # Create account with avatar and local_filename
        test_account = AccountFactory.build(
            id=12349,
            username="test_user_5",
        )
        session.add(test_account)

        # Create path to non-existent file in temp directory
        nonexistent_file = tmp_path / "nonexistent_avatar.jpg"
        # Don't create the file - just reference it

        # Create avatar media with non-existent file path
        avatar = MediaFactory.build(
            id=99996,
            accountId=12349,
            local_filename=str(nonexistent_file),  # File doesn't exist
        )
        session.add(avatar)
        await session.commit()

        # Link avatar to account
        await session.execute(
            account_avatar.insert().values(accountId=12349, mediaId=99996)
        )
        await session.commit()

        # Create GraphQL response for findImages
        # Use factory to create ImageFile, then convert to dict with datetime as string
        image_file = ImageFileFactory(
            id="456",
            path=str(nonexistent_file),
            basename="nonexistent_avatar.jpg",
            size=0,  # File doesn't exist
            width=800,
            height=600,
        )
        image_file_dict = sanitize_model_data(image_file.__dict__)
        # Convert datetime to ISO string for JSON serialization
        if image_file_dict.get("mod_time"):
            image_file_dict["mod_time"] = image_file_dict["mod_time"].isoformat()
        image_data = create_image_dict(
            id="789",
            title="Avatar",
            visual_files=[image_file_dict],
        )
        images_response = create_find_images_result(count=1, images=[image_data])

        # Mock findImages GraphQL response
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200, json=create_graphql_response("findImages", images_response)
            )
        )

        # Mock print_error and logger to verify error handling
        with (
            patch("stash.processing.mixins.account.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.account.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.account.debug_print") as mock_debug_print,
        ):
            # Call _update_performer_avatar - should handle FileNotFoundError
            await respx_stash_processor._update_performer_avatar(
                test_account, test_performer, session=session
            )

            # Verify error handling was triggered
            mock_print_error.assert_called_once()
            assert "Failed to update performer avatar" in str(
                mock_print_error.call_args
            )
            mock_logger_exception.assert_called_once()
            mock_debug_print.assert_called_once()
            assert "avatar_update_failed" in str(mock_debug_print.call_args)

        # Verify findImages was called but performerUpdate was NOT
        assert len(graphql_route.calls) == 1  # Only findImages
