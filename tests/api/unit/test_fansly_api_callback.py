"""Test for FanslyApi device update callback functionality"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from tests.fixtures.api.api_fixtures import dump_fansly_calls


class TestFanslyApiCallback:
    """Tests for the FanslyApi device update callback functionality."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_callback_when_update_needed(self):
        """Test that the callback is called when device ID is updated."""
        mock_callback = MagicMock()

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="initial_device_id",
            device_id_timestamp=int(datetime.now(UTC).timestamp() * 1000),
            on_device_updated=mock_callback,
        )

        respx.options(f"{FanslyApi.BASE_URL}device/id").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get(f"{FanslyApi.BASE_URL}device/id").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": "true", "response": "new_device_id"},
                )
            ]
        )

        api.device_id_timestamp = 0

        try:
            await api.update_device_id()
        finally:
            dump_fansly_calls(device_route.calls, "callback_when_update_needed")

        mock_callback.assert_called_once()
