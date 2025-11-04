"""Unit tests for StudioProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session


class TestStudioProcessingMixin:
    """Test the studio processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, studio_mixin, mock_account):
        """Test _find_existing_studio method."""
        # Mock process_creator_studio
        studio_mixin.process_creator_studio = AsyncMock()

        # Call _find_existing_studio
        await studio_mixin._find_existing_studio(mock_account)

        # Verify process_creator_studio was called with account and None performer
        studio_mixin.process_creator_studio.assert_called_once_with(
            account=mock_account, performer=None
        )

    @pytest.mark.asyncio
    async def test_process_creator_studio(
        self, studio_mixin, mock_account, mock_performer, mock_studio
    ):
        """Test process_creator_studio method."""
        # Mock session
        mock_session = MagicMock(spec=Session)

        # Test Case 1: Fansly Studio exists and Creator Studio exists
        # Mock find_studios for Fansly
        fansly_studio_result = MagicMock()
        fansly_studio_result.count = 1
        fansly_studio_dict = {"id": "fansly_123", "name": "Fansly (network)"}
        fansly_studio_result.studios = [fansly_studio_dict]

        # Mock find_studios for Creator
        creator_studio_result = MagicMock()
        creator_studio_result.count = 1
        creator_studio_dict = {"id": "studio_123", "name": "test_user (Fansly)"}
        creator_studio_result.studios = [creator_studio_dict]

        # Set up returns
        studio_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        # Call process_creator_studio
        result = await studio_mixin.process_creator_studio(
            account=mock_account, performer=mock_performer, session=mock_session
        )

        # Verify result and calls
        assert result is not None
        assert result.id == "studio_123"
        assert result.name == "test_user (Fansly)"

        # Verify find_studios calls
        assert studio_mixin.context.client.find_studios.call_count == 2
        studio_mixin.context.client.find_studios.assert_any_call(q="Fansly (network)")
        studio_mixin.context.client.find_studios.assert_any_call(q="test_user (Fansly)")

        # Test Case 2: Fansly Studio exists but Creator Studio doesn't
        studio_mixin.context.client.find_studios.reset_mock()

        # Mock find_studios for Fansly (same as before)
        fansly_studio_result.count = 1

        # Mock find_studios for Creator (not found)
        creator_studio_result.count = 0

        # Mock create_studio
        studio_mixin.context.client.create_studio = AsyncMock(return_value=mock_studio)

        # Set up returns
        studio_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        # Call process_creator_studio
        with patch("stash.processing.mixins.studio.print_info") as mock_print_info:
            result = await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer, session=mock_session
            )

            # Verify result and calls
            assert result == mock_studio

            # Verify find_studios calls
            assert studio_mixin.context.client.find_studios.call_count == 2
            studio_mixin.context.client.find_studios.assert_any_call(
                q="Fansly (network)"
            )
            studio_mixin.context.client.find_studios.assert_any_call(
                q="test_user (Fansly)"
            )

            # Verify create_studio was called
            studio_mixin.context.client.create_studio.assert_called_once()
            # Check if studio has correct properties
            call_arg = studio_mixin.context.client.create_studio.call_args[0][0]
            assert call_arg.id == "new"
            assert call_arg.name == "test_user (Fansly)"
            assert call_arg.url == "https://fansly.com/test_user"
            assert call_arg.performers == [mock_performer]

            # Verify print_info called
            mock_print_info.assert_called_once()
            assert "Created studio" in str(mock_print_info.call_args)

        # Test Case 3: Fansly Studio not found
        studio_mixin.context.client.find_studios.reset_mock()
        studio_mixin.context.client.create_studio.reset_mock()

        # Mock find_studios for Fansly (not found)
        fansly_studio_result.count = 0

        # Set up returns
        studio_mixin.context.client.find_studios = AsyncMock(
            return_value=fansly_studio_result
        )

        # Call process_creator_studio and expect error
        with pytest.raises(ValueError) as excinfo:  # noqa: PT011 - message validated by assertion below
            await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer, session=mock_session
            )

        # Verify error message
        assert "Fansly Studio not found in Stash" in str(excinfo.value)

        # Verify find_studios called once
        studio_mixin.context.client.find_studios.assert_called_once_with(
            q="Fansly (network)"
        )

        # Test Case 4: Creation fails with exception then succeeds on retry
        studio_mixin.context.client.find_studios.reset_mock()

        # Mock find_studios for Fansly (exists)
        fansly_studio_result.count = 1

        # Mock find_studios for Creator (not found then found)
        creator_studio_result_empty = MagicMock()
        creator_studio_result_empty.count = 0

        creator_studio_result_found = MagicMock()
        creator_studio_result_found.count = 1
        creator_studio_result_found.studios = [creator_studio_dict]

        # Set up returns
        studio_mixin.context.client.find_studios = AsyncMock(
            side_effect=[
                fansly_studio_result,
                creator_studio_result_empty,
                creator_studio_result_found,
            ]
        )

        # Mock create_studio to raise exception
        studio_mixin.context.client.create_studio.reset_mock()
        studio_mixin.context.client.create_studio.side_effect = Exception("Test error")

        # Call process_creator_studio with error mocks
        with (
            patch("stash.processing.mixins.studio.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.studio.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.studio.debug_print") as mock_debug_print,
        ):
            result = await studio_mixin.process_creator_studio(
                account=mock_account, performer=mock_performer, session=mock_session
            )

            # Verify result and error handling
            assert result is not None
            assert result.id == "studio_123"

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to create studio" in str(mock_print_error.call_args)
            mock_logger_exception.assert_called_once()
            # debug_print is called 3 times (fansly_studio_dict, fansly_studio, studio_creation_failed)
            assert mock_debug_print.call_count == 3
            assert "studio_creation_failed" in str(mock_debug_print.call_args)

            # Verify find_studios was called for retry
            assert studio_mixin.context.client.find_studios.call_count == 3
