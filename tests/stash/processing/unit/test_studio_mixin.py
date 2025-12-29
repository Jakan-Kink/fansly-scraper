"""Unit tests for StudioProcessingMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that GraphQL calls are made
with correct data from our test objects.
"""

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from tests.fixtures import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)


class TestStudioProcessingMixin:
    """Test the studio processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_process_creator_studio_both_exist(
        self, respx_stash_processor, mock_account, mock_performer
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

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
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

        # Call process_creator_studio (respx will intercept HTTP calls)
        result = await respx_stash_processor.process_creator_studio(
            account=mock_account, performer=mock_performer
        )

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 2, (
            f"Expected exactly 2 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 1: Find Fansly studio
        call1_body = json.loads(graphql_route.calls[0].request.content)
        assert "findStudios" in call1_body.get("query", "")
        assert call1_body["variables"]["filter"]["q"] == "Fansly (network)"

        # Call 2: Find Creator studio (found)
        call2_body = json.loads(graphql_route.calls[1].request.content)
        assert "findStudios" in call2_body.get("query", "")
        assert call2_body["variables"]["filter"]["q"] == "test_user (Fansly)"

        # Verify result
        assert result is not None
        assert result.id == "studio_123"
        assert result.name == "test_user (Fansly)"

    @pytest.mark.asyncio
    async def test_process_creator_studio_create_new(
        self, respx_stash_processor, mock_account, mock_performer, mock_studio
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
            urls=mock_studio.urls,
        )

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
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

        # Call process_creator_studio (respx will intercept HTTP calls)
        with patch("stash.processing.mixins.studio.print_info") as mock_print_info:
            result = await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # === PERMANENT GraphQL call sequence assertions ===
            assert len(graphql_route.calls) == 3, (
                f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
            )

            # Call 1: Find Fansly studio
            call1_body = json.loads(graphql_route.calls[0].request.content)
            assert "findStudios" in call1_body.get("query", "")
            assert call1_body["variables"]["filter"]["q"] == "Fansly (network)"

            # Call 2: Find Creator studio (not found)
            call2_body = json.loads(graphql_route.calls[1].request.content)
            assert "findStudios" in call2_body.get("query", "")
            assert call2_body["variables"]["filter"]["q"] == "test_user (Fansly)"

            # Call 3: Create studio
            call3_body = json.loads(graphql_route.calls[2].request.content)
            assert "studioCreate" in call3_body.get("query", "")
            assert call3_body["variables"]["input"]["name"] == "test_user (Fansly)"
            assert (
                "https://fansly.com/test_user"
                in call3_body["variables"]["input"]["urls"]
            )

            # Verify result
            assert result is not None
            # The result should be the created studio
            assert hasattr(result, "id")

            # Verify print_info called
            mock_print_info.assert_called_once()
            assert "Created studio" in str(mock_print_info.call_args)

    @pytest.mark.asyncio
    async def test_process_creator_studio_fansly_not_found(
        self, respx_stash_processor, mock_account, mock_performer
    ):
        """Test process_creator_studio when Fansly studio doesn't exist."""
        # Create empty response
        empty_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL response (respx_stash_processor already has respx enabled)
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findStudios", empty_result),
            )
        )

        # Call process_creator_studio and expect error (respx will intercept HTTP calls)
        with pytest.raises(
            ValueError, match=r"Fansly Studio not found in Stash"
        ) as excinfo:
            await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

        # Verify error message
        assert "Fansly Studio not found in Stash" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_process_creator_studio_creation_fails_then_retry(
        self, respx_stash_processor, mock_account, mock_performer
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
            urls=["https://fansly.com/test_user"],
            aliases=[],
            tags=[],
            stash_ids=[],
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio_dict]
        )

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
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

        # Call process_creator_studio with error mocks (respx will intercept HTTP calls)
        with (
            patch("stash.processing.mixins.studio.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.studio.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.studio.debug_print") as mock_debug_print,
        ):
            result = await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # === PERMANENT GraphQL call sequence assertions ===
            assert len(graphql_route.calls) == 4, (
                f"Expected exactly 4 GraphQL calls, got {len(graphql_route.calls)}"
            )

            # Call 1: Find Fansly studio
            call1_body = json.loads(graphql_route.calls[0].request.content)
            assert "findStudios" in call1_body.get("query", "")
            assert call1_body["variables"]["filter"]["q"] == "Fansly (network)"

            # Call 2: Find Creator studio (first attempt)
            call2_body = json.loads(graphql_route.calls[1].request.content)
            assert "findStudios" in call2_body.get("query", "")
            assert call2_body["variables"]["filter"]["q"] == "test_user (Fansly)"

            # Call 3: Create studio (will fail)
            call3_body = json.loads(graphql_route.calls[2].request.content)
            assert "studioCreate" in call3_body.get("query", "")
            assert call3_body["variables"]["input"]["name"] == "test_user (Fansly)"

            # Call 4: Retry find Creator studio
            call4_body = json.loads(graphql_route.calls[3].request.content)
            assert "findStudios" in call4_body.get("query", "")
            assert call4_body["variables"]["filter"]["q"] == "test_user (Fansly)"

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
