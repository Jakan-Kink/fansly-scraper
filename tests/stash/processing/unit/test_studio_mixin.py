"""Unit tests for StudioProcessingMixin."""

import contextlib
import httpx
import pytest
import respx
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)


class MockDatabase:
    """Minimal mock database to satisfy @with_session decorator."""

    @contextlib.asynccontextmanager
    async def async_session_scope(self):
        """Provide async session context manager."""
        # Yield a mock session (unused by process_creator_studio due to ARG002)
        session = MagicMock()
        yield session


class TestStudioProcessingMixin:
    """Test the studio processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, studio_mixin, mock_account):
        """Test _find_existing_studio method."""
        # Mock process_creator_studio (internal method, not GraphQL)
        studio_mixin.process_creator_studio = AsyncMock()

        # Call _find_existing_studio
        await studio_mixin._find_existing_studio(mock_account)

        # Verify process_creator_studio was called with account and None performer
        studio_mixin.process_creator_studio.assert_called_once_with(
            account=mock_account, performer=None
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_creator_studio_both_exist(
        self, studio_mixin, mock_account, mock_performer
    ):
        """Test process_creator_studio when both Fansly and Creator studios exist."""
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_studio_dict = create_studio_dict(
            id="studio_123", name="test_user (Fansly)"
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio_dict]
        )

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: find Creator studio
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", creator_studio_result),
                ),
            ]
        )

        # Initialize client
        await studio_mixin.context.get_client()

        # Provide mock database for @with_session decorator
        studio_mixin.database = MockDatabase()

        # Call process_creator_studio
        result = await studio_mixin.process_creator_studio(
            account=mock_account, performer=mock_performer
        )

        # Verify result
        assert result is not None
        assert result.id == "studio_123"
        assert result.name == "test_user (Fansly)"

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_creator_studio_create_new(
        self, studio_mixin, mock_account, mock_performer, mock_studio
    ):
        """Test process_creator_studio when Creator studio doesn't exist and needs to be created."""
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        empty_result = create_find_studios_result(count=0, studios=[])

        # Create studio for creation response
        new_studio_dict = create_studio_dict(
            id=mock_studio.id,
            name=mock_studio.name,
            url=mock_studio.url,
        )

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: find Creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                ),
                # Third call: studioCreate
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", new_studio_dict),
                ),
            ]
        )

        # Initialize client
        await studio_mixin.context.get_client()

        # Provide mock database for @with_session decorator
        studio_mixin.database = MockDatabase()

        # Call process_creator_studio
        with patch("stash.processing.mixins.studio.print_info") as mock_print_info:
            result = await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # Verify result
            assert result is not None
            # The result should be the created studio
            assert hasattr(result, "id")

            # Verify print_info called
            mock_print_info.assert_called_once()
            assert "Created studio" in str(mock_print_info.call_args)

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_creator_studio_fansly_not_found(
        self, studio_mixin, mock_account, mock_performer
    ):
        """Test process_creator_studio when Fansly studio doesn't exist."""
        # Create empty response
        empty_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL response
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findStudios", empty_result),
            )
        )

        # Initialize client
        await studio_mixin.context.get_client()

        # Provide mock database for @with_session decorator
        studio_mixin.database = MockDatabase()

        # Call process_creator_studio and expect error
        with pytest.raises(
            ValueError
        ) as excinfo:  # noqa: PT011 - message validated by assertion below
            await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

        # Verify error message
        assert "Fansly Studio not found in Stash" in str(excinfo.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_creator_studio_creation_fails_then_retry(
        self, studio_mixin, mock_account, mock_performer
    ):
        """Test process_creator_studio when creation fails, then succeeds on retry."""
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        empty_result = create_find_studios_result(count=0, studios=[])

        creator_studio_dict = create_studio_dict(
            id="studio_123",
            name="test_user (Fansly)",
            url="https://fansly.com/test_user",
            aliases=[],
            tags=[],
            stash_ids=[],
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio_dict]
        )

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: find Creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                ),
                # Third call: studioCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "Test error"}],
                        "data": None,
                    },
                ),
                # Fourth call: retry find Creator studio (found this time)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", creator_studio_result),
                ),
            ]
        )

        # Initialize client
        await studio_mixin.context.get_client()

        # Provide mock database for @with_session decorator
        studio_mixin.database = MockDatabase()

        # Call process_creator_studio with error mocks
        with (
            patch("stash.processing.mixins.studio.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.studio.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.studio.debug_print") as mock_debug_print,
        ):
            result = await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # Verify result (should get the existing studio from retry)
            assert result is not None
            assert result.id == "studio_123"

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to create studio" in str(mock_print_error.call_args)
            mock_logger_exception.assert_called_once()
            # debug_print is called 3 times
            assert mock_debug_print.call_count == 3
            assert "studio_creation_failed" in str(mock_debug_print.call_args)
