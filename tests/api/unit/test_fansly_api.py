"""Unit tests for FanslyApi class"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.fansly import FanslyApi


class TestFanslyApi:
    def test_init(self, fansly_api):
        """Test FanslyApi initialization with basic parameters"""
        assert fansly_api.token == "test_token"
        assert fansly_api.user_agent == "test_user_agent"
        assert fansly_api.check_key == "test_check_key"
        assert fansly_api.session_id == "null"
        assert hasattr(fansly_api, "device_id")
        assert hasattr(fansly_api, "device_id_timestamp")

    def test_init_with_device_info(self):
        """Test FanslyApi initialization with device ID parameters"""
        test_device_id = "test_device_id"
        test_timestamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
        mock_callback = MagicMock()

        api = FanslyApi(
            token="test_token",
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id=test_device_id,
            device_id_timestamp=test_timestamp,
            on_device_updated=mock_callback,
        )

        assert api.device_id == test_device_id
        assert api.device_id_timestamp == test_timestamp
        assert api.on_device_updated == mock_callback

    def test_get_text_accept(self, fansly_api):
        """Test get_text_accept returns correct accept header"""
        assert fansly_api.get_text_accept() == "application/json, text/plain, */*"

    def test_set_text_accept(self, fansly_api):
        """Test set_text_accept adds accept header correctly"""
        headers = {}
        fansly_api.set_text_accept(headers)
        assert headers["Accept"] == fansly_api.get_text_accept()

    def test_get_common_headers(self, fansly_api):
        """Test get_common_headers returns correct header structure"""
        headers = fansly_api.get_common_headers()

        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert headers["authorization"] == "test_token"
        assert headers["Origin"] == "https://fansly.com"
        assert headers["Referer"] == "https://fansly.com/"
        assert headers["User-Agent"] == "test_user_agent"

    def test_get_common_headers_alternate_token(self, fansly_api):
        """Test get_common_headers with alternate token"""
        alt_token = "alternate_token"
        headers = fansly_api.get_common_headers(alternate_token=alt_token)
        assert headers["authorization"] == alt_token

    def test_get_common_headers_missing_token(self):
        """Test get_common_headers raises error with missing token"""
        api = FanslyApi(
            token=None,
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="test_device_id",  # Provide device ID to avoid request
            device_id_timestamp=int(
                datetime.now(UTC).timestamp() * 1000
            ),  # Current timestamp
        )
        with pytest.raises(
            RuntimeError, match="Internal error generating HTTP headers"
        ):
            api.get_common_headers()

    def test_get_ngsw_params(self, fansly_api):
        """Test get_ngsw_params returns correct parameters"""
        params = fansly_api.get_ngsw_params()
        assert params == {"ngsw-bypass": "true"}

    def test_cyrb53(self, fansly_api):
        """Test cyrb53 hash function"""
        # Test with known input/output
        test_input = "test_string"
        hash1 = fansly_api.cyrb53(test_input)
        hash2 = fansly_api.cyrb53(test_input)

        # Same input should produce same hash
        assert hash1 == hash2

        # Different inputs should produce different hashes
        different_hash = fansly_api.cyrb53("different_string")
        assert hash1 != different_hash

    def test_cyrb53_with_seed(self, fansly_api):
        """Test cyrb53 hash function with seed"""
        test_input = "test_string"
        hash1 = fansly_api.cyrb53(test_input, seed=1)
        hash2 = fansly_api.cyrb53(test_input, seed=2)

        # Same input with different seeds should produce different hashes
        assert hash1 != hash2

    def test_get_timestamp_ms(self, fansly_api):
        """Test get_timestamp_ms returns current timestamp in milliseconds"""
        timestamp = fansly_api.get_timestamp_ms()
        now = int(datetime.now(UTC).timestamp() * 1000)
        # Allow 1 second difference due to execution time
        assert abs(timestamp - now) < 1000

    def test_get_client_timestamp(self, fansly_api):
        """Test get_client_timestamp returns value within expected range"""
        timestamp = fansly_api.get_client_timestamp()
        now = int(datetime.now(UTC).timestamp() * 1000)
        # Should be current time +/- 5000ms
        assert abs(timestamp - now) <= 5000

    def test_update_client_timestamp(self, fansly_api):
        """Test update_client_timestamp updates when newer"""
        old_timestamp = fansly_api.client_timestamp
        fansly_api.update_client_timestamp()
        assert fansly_api.client_timestamp >= old_timestamp

    def test_to_str16(self, fansly_api):
        """Test to_str16 hex conversion"""
        test_num = 255
        result = fansly_api.to_str16(test_num)
        assert result == "ff"

    def test_int32(self, fansly_api):
        """Test int32 conversion"""
        # Test within 32-bit range
        assert fansly_api.int32(100) == 100
        # Test overflow
        assert fansly_api.int32(2**31 + 1) < 2**31

    @pytest.mark.asyncio
    async def test_setup_session(self, fansly_api, mock_http_session):
        """Test setup_session success path"""
        # Mock the account info response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {"success": "true", "response": {}}
        mock_http_session.get.return_value = mock_response

        # Create a mock websocket instance with proper async methods
        mock_ws_instance = AsyncMock()
        mock_ws_instance.__aenter__.return_value = mock_ws_instance
        mock_ws_instance.recv.return_value = (
            '{"t":1,"d":"{\\"session\\":{\\"id\\":\\"test_session_id\\"}}"}'
        )

        # Mock the websocket connection
        with patch("websockets.client.connect", return_value=mock_ws_instance):
            result = await fansly_api.setup_session()
            assert result is True
            assert fansly_api.session_id == "test_session_id"

    def test_validate_json_response_success(self, fansly_api):
        """Test validate_json_response with successful response"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": "true"}

        assert fansly_api.validate_json_response(mock_response) is True

    def test_validate_json_response_failure(self, fansly_api):
        """Test validate_json_response with failed response"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": "false"}

        with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
            fansly_api.validate_json_response(mock_response)

    def test_get_json_response_contents(self, fansly_api):
        """Test get_json_response_contents extracts response field"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": "true",
            "response": {"data": "test_data"},
        }

        result = fansly_api.get_json_response_contents(mock_response)
        assert result == {"data": "test_data"}

    def test_get_client_user_name(self, fansly_api, mock_http_session):
        """Test get_client_user_name success path"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {
            "success": "true",
            "response": {"account": {"username": "test_user"}},
        }
        mock_http_session.get.return_value = mock_response

        assert fansly_api.get_client_user_name() == "test_user"

    def test_get_with_ngsw(self, fansly_api, mock_http_session):
        """Test get_with_ngsw builds correct request"""
        test_url = "https://api.test.com/endpoint"
        test_params = {"test": "param"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_with_ngsw(
            url=test_url, params=test_params, add_fansly_headers=True
        )

        # Verify options request was made
        mock_http_session.options.assert_called_once()

        # Verify GET request was made with correct parameters
        args = mock_http_session.get.call_args
        expected_params = test_params.copy()
        expected_params["ngsw-bypass"] = "true"
        assert args[1]["params"] == expected_params
        assert args[1]["headers"]["Origin"] == "https://fansly.com"

    def test_get_creator_account_info_single(self, fansly_api, mock_http_session):
        """Test get_creator_account_info with single username"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_creator_account_info("test_creator")

        args = mock_http_session.get.call_args
        assert args[1]["params"] == {"usernames": "test_creator", "ngsw-bypass": "true"}

    def test_get_creator_account_info_multiple(self, fansly_api, mock_http_session):
        """Test get_creator_account_info with multiple usernames"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_creator_account_info(["creator1", "creator2"])

        args = mock_http_session.get.call_args
        assert args[1]["params"] == {
            "usernames": "creator1,creator2",
            "ngsw-bypass": "true",
        }

    def test_get_account_info_by_id_single(self, fansly_api, mock_http_session):
        """Test get_account_info_by_id with single ID"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_account_info_by_id(123)

        args = mock_http_session.get.call_args
        assert args[1]["params"] == {"ids": "123", "ngsw-bypass": "true"}

    def test_get_account_info_by_id_multiple(self, fansly_api, mock_http_session):
        """Test get_account_info_by_id with multiple IDs"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_account_info_by_id([123, 456])

        args = mock_http_session.get.call_args
        assert args[1]["params"] == {"ids": "123,456", "ngsw-bypass": "true"}

    def test_get_media_collections(self, fansly_api, mock_http_session):
        """Test get_media_collections request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_media_collections()

        args = mock_http_session.get.call_args
        assert args[1]["params"]["limit"] == "9999"
        assert args[1]["params"]["offset"] == "0"

    def test_get_following_list(self, fansly_api, mock_http_session):
        """Test get_following_list with default parameters"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_following_list("user123")

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["limit"] == "425"
        assert params["offset"] == "0"
        assert params["before"] == "0"
        assert params["after"] == "0"

    def test_get_following_list_with_params(self, fansly_api, mock_http_session):
        """Test get_following_list with custom parameters"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_following_list(
            "user123", limit=10, offset=5, before=1000, after=500
        )

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["limit"] == "10"
        assert params["offset"] == "5"
        assert params["before"] == "1000"
        assert params["after"] == "500"

    def test_get_account_media(self, fansly_api, mock_http_session):
        """Test get_account_media request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_account_media("media123,media456")

        args = mock_http_session.get.call_args
        # The media IDs should be part of the parameters, not the URL
        assert args[1]["params"]["ids"] == "media123,media456"

    def test_get_post(self, fansly_api, mock_http_session):
        """Test get_post request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_post("post123")

        args = mock_http_session.get.call_args
        assert args[1]["params"]["ids"] == "post123"

    def test_get_timeline(self, fansly_api, mock_http_session):
        """Test get_timeline request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_timeline("creator123", "cursor123")

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["before"] == "cursor123"
        assert params["after"] == "0"
        assert params["wallId"] == ""
        assert params["contentSearch"] == ""

    def test_get_wall_posts(self, fansly_api, mock_http_session):
        """Test get_wall_posts request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_wall_posts("creator123", "wall123", "cursor456")

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["before"] == "cursor456"
        assert params["after"] == "0"
        assert params["wallId"] == "wall123"
        assert params["contentSearch"] == ""

    def test_get_group(self, fansly_api, mock_http_session):
        """Test get_group request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_group()

        mock_http_session.get.assert_called_once()
        assert "messaging/groups" in mock_http_session.get.call_args[1]["url"]

    def test_get_message(self, fansly_api, mock_http_session):
        """Test get_message request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        test_params = {"param1": "value1"}
        expected_params = test_params.copy()
        expected_params["ngsw-bypass"] = "true"
        fansly_api.get_message(test_params)

        args = mock_http_session.get.call_args
        assert args[1]["params"] == expected_params

    def test_get_device_id(self, fansly_api, mock_http_session):
        """Test get_device_id request"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {
            "success": "true",
            "response": "test_device_id",
        }
        mock_http_session.get.return_value = mock_response

        result = fansly_api.get_device_id()
        assert result == "test_device_id"

    def test_update_device_id_within_timeframe(self, fansly_api):
        """Test update_device_id doesn't update if within time window"""
        original_device_id = fansly_api.device_id
        current_ts = fansly_api.get_timestamp_ms()
        fansly_api.device_id_timestamp = current_ts

        updated_id = fansly_api.update_device_id()
        assert updated_id == original_device_id

    def test_update_device_id_expired(self, fansly_api, mock_http_session):
        """Test update_device_id updates when timestamp expired"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {
            "success": "true",
            "response": "new_device_id",
        }
        mock_http_session.get.return_value = mock_response

        # Set old timestamp
        fansly_api.device_id_timestamp = 0

        # Mock callback
        mock_callback = MagicMock()
        fansly_api.on_device_updated = mock_callback

        updated_id = fansly_api.update_device_id()
        assert updated_id == "new_device_id"
        mock_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_session_error(self, fansly_api, mock_http_session):
        """Test setup_session handles errors"""
        # Mock HTTP response failure
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.reason_phrase = "Unauthorized"
        mock_http_session.get.return_value = mock_response

        # Mock websocket to raise exception
        with patch("websockets.client.connect") as mock_ws:
            mock_ws.side_effect = Exception("Connection failed")

            with pytest.raises(RuntimeError, match="Error during session setup"):
                await fansly_api.setup_session()

    def test_get_http_headers_with_session(self, fansly_api):
        """Test get_http_headers includes session ID when available"""
        fansly_api.session_id = "test_session"
        headers = fansly_api.get_http_headers(
            url="https://test.com", add_fansly_headers=True
        )
        assert headers["fansly-session-id"] == "test_session"

    def test_validate_json_response_non_200(self, fansly_api):
        """Test validate_json_response with non-200 status"""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.reason = "Not Found"

        with pytest.raises(RuntimeError, match="Web request failed: 404"):
            fansly_api.validate_json_response(mock_response)
