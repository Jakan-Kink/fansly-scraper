"""Unit tests for metadata.media_utils module."""

import pytest

from metadata.media_utils import process_preview, validate_media_id
from metadata.models import Media
from tests.fixtures.utils.test_isolation import snowflake_id


class TestValidateMediaIdEdgeCases:
    """Cover metadata/media_utils.py lines 65-75: string passes isdigit() but fails int()."""

    def test_unicode_digit_string(self):
        """Lines 65-75: Unicode superscript digits pass isdigit() but fail int().
        '²'.isdigit() → True, int('²') → ValueError → lines 65-75 catch it."""
        result = validate_media_id("²", context_id=1)
        assert result is None

    def test_normal_string_hits_non_digit_path(self):
        """Lines 50-61: non-digit string → isdigit() False → early return."""
        assert validate_media_id("not_numeric", context_id=1) is None

    def test_valid_snowflake_string(self):
        """Lines 63-77: valid digit string → int() succeeds → range check."""
        sid = snowflake_id()
        assert validate_media_id(str(sid), context_id=1) == sid


class TestProcessPreviewEdgeCases:
    """Cover metadata/media_utils.py 120→exit: string preview_data → not a dict → falls through."""

    @pytest.mark.asyncio
    async def test_string_preview_falls_through(
        self, entity_store, mock_config, test_account
    ):
        """Line 120→exit: non-empty string preview passes earlier checks but
        isinstance(preview_data, dict) is False → no processing."""
        parent = Media(id=snowflake_id(), accountId=test_account.id)
        await process_preview(mock_config, parent, "12345")
