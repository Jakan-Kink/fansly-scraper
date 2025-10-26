"""Test for FanslyApi device update callback functionality"""

from datetime import datetime
from unittest.mock import MagicMock

from api.fansly import FanslyApi


class TestFanslyApiCallback:
    """Tests for the FanslyApi device update callback functionality."""

    def test_callback_when_update_needed(self):
        """Test that the callback is called when device ID is updated."""
        # Create a mock callback
        mock_callback = MagicMock()

        # Initialize API with device info to avoid update during initialization
        api = FanslyApi(
            token="test_token",
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="initial_device_id",
            device_id_timestamp=int(datetime.now().timestamp() * 1000),
            on_device_updated=mock_callback,
        )

        # Replace get_device_id with a mock to avoid real API calls
        api.get_device_id = MagicMock(return_value="new_device_id")

        # Set timestamp to a very old value to trigger update
        api.device_id_timestamp = 0

        # Call the method
        api.update_device_id()

        # Verify callback was called
        mock_callback.assert_called_once()
