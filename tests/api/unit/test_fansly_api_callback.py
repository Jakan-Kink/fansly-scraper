"""Test for FanslyApi device update callback functionality"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import respx

from api.fansly import FanslyApi


class TestFanslyApiCallback:
    """Tests for the FanslyApi device update callback functionality."""

    @respx.mock
    def test_callback_when_update_needed(self):
        """Test that the callback is called when device ID is updated."""
        # Create a mock callback (mocking user-provided callback is appropriate)
        mock_callback = MagicMock()

        # Initialize API with device info to avoid update during initialization
        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="initial_device_id",
            device_id_timestamp=int(datetime.now(UTC).timestamp() * 1000),
            on_device_updated=mock_callback,
        )

        # Mock the HTTP response for get_device_id at the edge
        respx.get(url__regex=r".*api/v1/check/device.*").mock(
            return_value=httpx.Response(
                200,
                json={"success": True, "response": {"deviceId": "new_device_id"}},
            )
        )

        # Set timestamp to a very old value to trigger update
        api.device_id_timestamp = 0

        # Call the method
        api.update_device_id()

        # Verify callback was called
        mock_callback.assert_called_once()
