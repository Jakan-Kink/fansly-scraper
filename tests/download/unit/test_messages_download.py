"""Tests for download/messages.py — download_messages orchestrator."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from download.downloadstate import DownloadState
from download.messages import download_messages
from download.types import DownloadType
from tests.fixtures.utils.test_isolation import snowflake_id


class TestDownloadMessages:
    """Tests for download_messages — group lookup, message pagination, failure paths.

    Uses respx_fansly_api fixture. Patches metadata processing functions
    (process_groups_response, process_messages_metadata) and download pipeline
    (fetch_and_process_media, process_download_accessible_media).
    """

    @pytest.mark.asyncio
    async def test_success_with_messages(self, respx_fansly_api, mock_config):
        """Lines 19-92: success — find group, fetch messages, paginate until IndexError."""
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False

        creator_id = snowflake_id()
        group_id = snowflake_id()
        msg_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "data": [],
                            "aggregationData": {
                                "groups": [
                                    {
                                        "id": group_id,
                                        "createdBy": creator_id,
                                        "users": [{"userId": creator_id}],
                                    }
                                ],
                                "accounts": [],
                            },
                        },
                    },
                )
            ],
        )

        respx.get("https://apiv3.fansly.com/api/v1/message").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "messages": [
                                {
                                    "id": msg_id,
                                    "senderId": creator_id,
                                    "content": "hi",
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                }
                            ],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                        },
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "messages": [],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                        },
                    },
                ),
            ],
        )

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"msg_{creator_id}"

        with (
            patch("download.messages.process_groups_response", new_callable=AsyncMock),
            patch(
                "download.messages.process_messages_metadata", new_callable=AsyncMock
            ),
            patch(
                "download.messages.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.messages.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("download.messages.sleep", new_callable=AsyncMock),
        ):
            await download_messages(mock_config, state)

        assert state.download_type == DownloadType.MESSAGES

    @pytest.mark.asyncio
    async def test_no_group_found(self, respx_fansly_api, mock_config):
        """Lines 101-105: no chat history with creator → warning."""
        creator_id = snowflake_id()
        other_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "data": [],
                            "aggregationData": {
                                "groups": [
                                    {
                                        "id": snowflake_id(),
                                        "createdBy": other_id,
                                        "users": [{"userId": other_id}],
                                    }
                                ],
                                "accounts": [],
                            },
                        },
                    },
                )
            ],
        )

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = "nochat_creator"

        with patch("download.messages.process_groups_response", new_callable=AsyncMock):
            await download_messages(mock_config, state)

    @pytest.mark.asyncio
    async def test_groups_api_failure(self, respx_fansly_api, mock_config):
        """Lines 107-113: groups API non-200 → error + input_enter_continue."""
        mock_config.interactive = False

        respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            side_effect=[httpx.Response(403, text="Forbidden")],
        )

        state = DownloadState()
        state.creator_id = snowflake_id()

        with patch("download.messages.input_enter_continue"):
            await download_messages(mock_config, state)

    @pytest.mark.asyncio
    async def test_message_page_failure(self, respx_fansly_api, mock_config):
        """Lines 94-99: message page non-200 → error, loop ends."""
        creator_id = snowflake_id()
        group_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "data": [],
                            "aggregationData": {
                                "groups": [
                                    {
                                        "id": group_id,
                                        "createdBy": creator_id,
                                        "users": [{"userId": creator_id}],
                                    }
                                ],
                                "accounts": [],
                            },
                        },
                    },
                )
            ],
        )
        respx.get("https://apiv3.fansly.com/api/v1/message").mock(
            side_effect=[httpx.Response(403, text="Forbidden")],
        )

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"mfail_{creator_id}"

        with patch("download.messages.process_groups_response", new_callable=AsyncMock):
            await download_messages(mock_config, state)
