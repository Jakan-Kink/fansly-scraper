"""Unit tests for AccountProcessingMixin.

These tests use respx_stash_processor fixture for edge mocking.
"""

import logging
import tempfile
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image
from stash_graphql_client.types import Image as SGCImage

from metadata import PostMention
from tests.fixtures.metadata.metadata_factories import AccountFactory, MediaFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_graphql_response,
    create_image_dict,
    create_performer_dict,
)
from tests.fixtures.stash.stash_type_factories import PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestAccountProcessingMixin:
    """Test the account processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_find_account(self, respx_stash_processor, entity_store, caplog):
        """Test _find_account method.

        This test doesn't require GraphQL mocking since it only tests database queries.
        """
        acct_id = snowflake_id()

        # Create test account in entity_store (production code uses get_store())
        account = AccountFactory.build(id=acct_id, username="test_user", stash_id=12345)
        await entity_store.save(account)

        # Set creator_id to match the account we just created
        respx_stash_processor.state.creator_id = acct_id

        # Call _find_account with creator_id
        await respx_stash_processor.context.get_client()
        found_account = await respx_stash_processor._find_account()

        # Verify account was found
        assert found_account is not None
        assert found_account.id == acct_id
        assert found_account.username == "test_user"

        # Test with creator_name instead of id
        respx_stash_processor.state.creator_id = None

        # Call _find_account again
        found_account = await respx_stash_processor._find_account()

        # Verify account was found by username
        assert found_account is not None
        assert found_account.username == "test_user"

        # Test with no account found
        respx_stash_processor.state.creator_name = "nonexistent_user"

        # Call _find_account
        caplog.set_level(logging.WARNING)
        found_account = await respx_stash_processor._find_account()

        # Verify no account and warning was printed (loguru routes to caplog)
        assert found_account is None
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("nonexistent_user" in msg for msg in warnings)

    @pytest.mark.asyncio
    async def test_process_creator(self, respx_stash_processor, entity_store):
        """Test process_creator method."""
        acct_id = snowflake_id()

        # Create test account in entity_store
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=None,
            displayName=None,  # Explicitly set to None to test username fallback
        )
        await entity_store.save(account)

        # Set creator_id to match the account we just created
        respx_stash_processor.state.creator_id = acct_id

        # Setup edge mock for get_or_create_performer flow:
        # 1. findPerformers (fuzzy search) returns empty
        # 2. performerCreate creates new performer
        await respx_stash_processor.context.get_client()

        performer_dict = create_performer_dict(
            id="123",
            name="test_user",
            urls=["https://fansly.com/test_user"],
        )

        # Mock GraphQL HTTP responses
        # _get_or_create_performer makes 3 findPerformers calls (name, alias, URL) + 1 create
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers (name search) - no existing performer
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers (alias search) - no existing performer
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers (URL search) - no existing performer
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # performerCreate - create new performer
                httpx.Response(
                    200,
                    json=create_graphql_response("performerCreate", performer_dict),
                ),
            ]
        )

        # Call process_creator (no session= parameter)
        try:
            result_account, performer = await respx_stash_processor.process_creator()
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_creator")

        # Verify results
        assert result_account.id == account.id
        assert performer.id == "123"
        assert performer.name == "test_user"

        # Verify GraphQL calls were made
        assert (
            graphql_route.call_count == 4
        )  # 3x findPerformers (name/alias/URL) + performerCreate

        # Test with no account found
        respx_stash_processor.state.creator_id = (
            None  # Clear creator_id to force username lookup
        )
        respx_stash_processor.state.creator_name = "nonexistent"

        # Call process_creator and expect error
        try:
            with pytest.raises(ValueError) as excinfo:
                await respx_stash_processor.process_creator()
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_creator_no_account")

        # Verify error message
        assert "No account found for creator" in str(excinfo.value)
        assert "nonexistent" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_update_performer_avatar(self, respx_stash_processor):
        """Test _update_performer_avatar method."""
        acct_id = snowflake_id()
        avatar_media_id = snowflake_id()

        # Create account with no avatar (avatar=None by default)
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=12345,
        )

        # Call _update_performer_avatar with no avatar
        await respx_stash_processor.context.get_client()

        mock_performer = PerformerFactory.build(
            id="123",
            name="test_user",
        )

        await respx_stash_processor._update_performer_avatar(account, mock_performer)

        # Verify no GraphQL calls (no avatar to update)
        assert len(respx.calls) == 0

        # Now set avatar on account (Pydantic relationship — direct attribute)
        avatar = MediaFactory.build(
            id=avatar_media_id,
            accountId=account.id,
            local_filename="avatar.jpg",
        )
        account.avatar = avatar

        # Mock performer with default image
        mock_performer.image_path = "default=true"

        # Create a temporary 2x2 red image file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
            temp_avatar_path = Path(tmp_file.name)
            img = Image.new("RGB", (2, 2), color="red")
            img.save(temp_avatar_path, "JPEG")

        try:
            # Create image dict with visual_files
            image_dict = create_image_dict(
                id="48000",
                title=None,
                visual_files=[
                    {
                        "id": "48100",
                        "path": str(temp_avatar_path),
                        "basename": "avatar.jpg",
                        "parent_folder_id": "folder_123",
                        "mod_time": "2024-01-01T00:00:00Z",
                        "size": 1024,
                        "fingerprints": [],
                        "width": 100,
                        "height": 100,
                    }
                ],
            )

            images_response = {
                "count": 1,
                "images": [image_dict],
                "megapixels": 0.0,
                "filesize": 0.0,
            }

            performer_dict = create_performer_dict(
                id="123",
                name="test_user",
            )

            graphql_route = respx.post("http://localhost:9999/graphql").mock(
                side_effect=[
                    # findImages - find avatar image
                    httpx.Response(
                        200,
                        json=create_graphql_response("findImages", images_response),
                    ),
                    # performerUpdate - update avatar
                    httpx.Response(
                        200,
                        json=create_graphql_response("performerUpdate", performer_dict),
                    ),
                ]
            )

            # Call _update_performer_avatar (no session= parameter)
            try:
                await respx_stash_processor._update_performer_avatar(
                    account, mock_performer
                )
            finally:
                dump_graphql_calls(graphql_route.calls, "test_update_performer_avatar")

            # Verify GraphQL calls were made
            assert graphql_route.call_count == 2  # findImages + performerUpdate
        finally:
            # Clean up temp file
            temp_avatar_path.unlink(missing_ok=True)  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_id(self, respx_stash_processor):
        """Test _find_existing_performer finds performer by stash_id."""
        acct_id = snowflake_id()

        # Create account with stash_id — just an in-memory object, no DB needed
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=999,
        )

        # Setup context.client
        await respx_stash_processor.context.get_client()

        performer_dict = create_performer_dict(id="999", name="test_user")

        # Mock GraphQL response - findPerformer by ID (singular)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformer", performer_dict),
                ),
            ]
        )

        try:
            performer = await respx_stash_processor._find_existing_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_find_existing_performer_by_id"
            )

        # Verify performer was found
        assert performer.id == "999"
        assert performer.name == "test_user"
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_name(self, respx_stash_processor):
        """Test _find_existing_performer finds performer by username."""
        acct_id = snowflake_id()

        # Create account without stash_id
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=None,
        )

        # Setup context.client
        await respx_stash_processor.context.get_client()

        performer_dict = create_performer_dict(id="999", name="test_user")

        # Mock GraphQL response - findPerformers (plural) by name
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 1, "performers": [performer_dict]}
                    ),
                ),
            ]
        )

        try:
            performer = await respx_stash_processor._find_existing_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_find_existing_performer_by_name"
            )

        # Verify performer was found by username
        assert performer.id == "999"
        assert performer.name == "test_user"
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "make_entity",
        [
            pytest.param(
                lambda: AccountFactory.build(
                    id=snowflake_id(), username="dual_user", stash_id=None
                ),
                id="account_by_username",
            ),
            pytest.param(
                lambda: PostMention(
                    id=snowflake_id(), postId=snowflake_id(), handle="dual_user"
                ),
                id="postmention_by_handle",
            ),
        ],
    )
    async def test_find_existing_performer_accepts_account_and_postmention(
        self, respx_stash_processor, make_entity
    ):
        """Both arms of the ``Account | PostMention`` param resolve a performer.

        Account resolves via ``username``, PostMention via ``handle`` (the
        isinstance-narrowed branch). One backing name lets a single
        findPerformers mock serve either type. Guards against re-narrowing the
        param back to ``Account`` (which would drop the mention path).
        """
        entity = make_entity()
        await respx_stash_processor.context.get_client()

        performer_dict = create_performer_dict(id="999", name="dual_user")
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 1, "performers": [performer_dict]},
                    ),
                ),
            ]
        )

        try:
            performer = await respx_stash_processor._find_existing_performer(entity)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_find_existing_performer_dual_type"
            )

        assert performer is not None
        assert performer.name == "dual_user"
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_find_existing_performer_not_found(self, respx_stash_processor):
        """Test _find_existing_performer returns None when not found."""
        acct_id = snowflake_id()

        # Create account without stash_id
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=None,
        )

        # Setup context.client
        await respx_stash_processor.context.get_client()

        # Mock GraphQL response - store.find_one() makes single call by name
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
            ]
        )

        try:
            performer = await respx_stash_processor._find_existing_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_find_existing_performer_not_found"
            )

        # Verify performer is None
        assert performer is None
        # Note: Library uses find_one() which makes 1 call (not 2)
        assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_update_account_stash_id(self, respx_stash_processor, entity_store):
        """Test _update_account_stash_id method.

        This test doesn't require GraphQL mocking since it only updates the database.
        """
        acct_id = snowflake_id()

        # Create account in entity_store (production code uses get_store())
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=None,
        )
        await entity_store.save(account)

        # Create mock performer
        mock_performer = PerformerFactory.build(id="123", name="test_user")

        # Call _update_account_stash_id (no session= parameter)
        await respx_stash_processor.context.get_client()
        await respx_stash_processor._update_account_stash_id(account, mock_performer)

        # Verify stash_id was updated (performer.id is string "123", converted to int)
        assert account.stash_id == int(mock_performer.id)

    @pytest.mark.asyncio
    async def test_get_or_create_performer_found_by_alias_raw_syntax(
        self, respx_stash_processor
    ):
        """Real _get_or_create_performer resolves a performer via the alias branch.

        Drives the REAL method (no reimplementation) over the respx Stash edge:
        the name search returns empty, so production falls through to the alias
        search (``store.find_one(Performer, aliases__contains=username)`` at
        account.py:145), which returns a hit. Asserts the found performer is
        returned without a create.

        Merged from the former ``_found_by_alias_django_style`` test, which had
        swapped in a test-authored ``django_style_method`` reimplementation and
        therefore exercised ZERO production lines. Both old tests differed only
        in the GraphQL filter SYNTAX they narrated; production emits exactly one
        path (``aliases__contains``), so the reimplementation variant was
        dropped and this real-method test is the single source of truth for the
        alias-resolution branch.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")

        # Create performer data that will be found by alias
        existing_performer_dict = create_performer_dict(
            id="999", name="Test User", aliases=["test_user"]
        )

        # Mock GraphQL: first call finds nothing by name, second call finds by alias
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers by name - not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 0, "performers": []},
                    ),
                ),
                # findPerformers by alias using raw syntax - FOUND
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 1, "performers": [existing_performer_dict]},
                    ),
                ),
            ]
        )

        await respx_stash_processor.context.get_client()
        try:
            result = await respx_stash_processor._get_or_create_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_get_or_create_performer_found_by_alias_raw_syntax",
            )

        # Verify performer was found (not created) via the alias branch.
        assert graphql_route.called
        assert result.id == "999"
        assert graphql_route.call_count == 2  # Name search + alias search only

    @pytest.mark.asyncio
    async def test_get_or_create_performer_found_by_url(self, respx_stash_processor):
        """Test _get_or_create_performer when performer found by URL (lines 131-132)."""
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")

        # Create performer data that will be found by URL
        existing_performer_dict = create_performer_dict(
            id="888", name="Different Name", urls=["https://fansly.com/test_user"]
        )

        # Mock GraphQL: name and alias searches fail, URL search succeeds
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers by name - not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 0, "performers": []},
                    ),
                ),
                # findPerformers by alias - not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 0, "performers": []},
                    ),
                ),
                # findPerformers by URL - FOUND (lines 131-132)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        {"count": 1, "performers": [existing_performer_dict]},
                    ),
                ),
            ]
        )

        await respx_stash_processor.context.get_client()
        try:
            result = await respx_stash_processor._get_or_create_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_get_or_create_performer_found_by_url"
            )

        # Verify performer was found by URL
        assert result.id == "888"
        assert graphql_route.call_count == 3  # Name + alias + URL searches

    @pytest.mark.asyncio
    async def test_update_performer_avatar_with_custom_image(
        self, respx_stash_processor
    ):
        """Test _update_performer_avatar when performer has custom image (line 242->exit)."""
        acct_id = snowflake_id()
        avatar_media_id = snowflake_id()

        # Create account with avatar set directly (Pydantic relationship)
        account = AccountFactory.build(id=acct_id, username="test_user")
        avatar = MediaFactory.build(
            id=avatar_media_id,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename="avatar.jpg",
        )
        account.avatar = avatar

        # Create performer with custom image (not default)
        performer = PerformerFactory.build(
            id="123",
            name="test_user",
            image_path="/path/to/custom_image.jpg",  # Custom image, no default=true
        )

        # Call _update_performer_avatar (no session= parameter)
        await respx_stash_processor.context.get_client()
        await respx_stash_processor._update_performer_avatar(account, performer)

        # Should return early without making any GraphQL calls
        # (line 242->exit branch)

    @pytest.mark.asyncio
    async def test_find_existing_performer_stash_id_returns_none(
        self, respx_stash_processor
    ):
        """Test _find_existing_performer when stash_id lookup returns None (line 302->314)."""
        acct_id = snowflake_id()
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=999,  # Has stash_id but lookup fails
        )

        # Mock HTTP responses - stash_id lookup fails (404), username search succeeds
        await respx_stash_processor.context.get_client()
        performer_dict = {"id": "123", "name": "test_user"}

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # store.get() for stash_id - not found (returns empty)
                httpx.Response(404, json={"error": "Not Found"}),
                # store.find_one() by username - found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 1, "performers": [performer_dict]}
                    ),
                ),
            ]
        )

        try:
            result = await respx_stash_processor._find_existing_performer(account)
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_find_existing_performer_stash_id_returns_none",
            )

        # Verify it tried stash_id first (failed), then username (succeeded)
        # Note: store.get() fails with exception when not found, triggers fallback
        assert result is not None
        assert result.id == "123"

    @pytest.mark.asyncio
    async def test_get_or_create_performer_alias_cache_hit(self, respx_stash_processor):
        """_get_or_create_performer finds performer by alias in cache (lines 141-142).

        When a performer is already in the store cache with an alias matching
        the username, the alias cache lookup succeeds without a GraphQL call.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="alias_user")

        # Pre-populate store cache with a performer whose alias matches the username
        cached_performer = PerformerFactory.build(
            id="5704",
            name="Real Name",
            alias_list=["alias_user"],
        )
        # Save to store to populate identity map cache
        performer_dict = create_performer_dict(
            id="5704",
            name="Real Name",
            aliases=["alias_user"],
        )
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # performerUpdate from store.save()
                httpx.Response(
                    200,
                    json=create_graphql_response("performerUpdate", performer_dict),
                ),
                # findPerformers by name — cache filter doesn't match name, falls to GraphQL
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
            ]
        )

        try:
            await respx_stash_processor.store.save(cached_performer)
            result = await respx_stash_processor._get_or_create_performer(account)
        finally:
            dump_graphql_calls(route.calls, "test_alias_cache_hit")

        assert result is not None
        assert result.name == "Real Name"

    @pytest.mark.asyncio
    async def test_find_existing_performer_stash_id_exception(
        self, respx_stash_processor
    ):
        """_find_existing_performer handles exception in stash_id lookup (lines 321-322).

        When store.get() raises an exception (e.g., GraphQL error),
        the exception is caught and fallback to name search occurs.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            stash_id=999,
        )

        await respx_stash_processor.context.get_client()

        performer_dict = create_performer_dict(id="5702", name="test_user")

        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # store.get_cached → None (not in cache)
                # store.get() → GraphQL error
                httpx.Response(
                    200,
                    json={"errors": [{"message": "performer not found"}], "data": None},
                ),
                # Fallback: store.find_one() by name → found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 1, "performers": [performer_dict]}
                    ),
                ),
            ]
        )

        try:
            result = await respx_stash_processor._find_existing_performer(account)
        finally:
            dump_graphql_calls(route.calls, "test_stash_id_exception")

        assert result is not None
        assert result.id == "5702"

    @pytest.mark.asyncio
    async def test_get_or_create_performer_url_cache_hit(self, respx_stash_processor):
        """Lines 148-154: _get_or_create_performer URL cache hit (zero GraphQL calls).

        Pre-loads a Performer with the matching fansly_url into the store
        cache. Name and alias cache filters miss; URL filter HITS — no
        GraphQL search needed.
        """
        acct_id = snowflake_id()
        username = f"cached_user_{acct_id}"
        account = AccountFactory.build(id=acct_id, username=username)
        fansly_url = f"https://fansly.com/{username}"

        # Pre-load a performer with a different name/no alias but matching URL.
        # Performer.id must be numeric (StashObject validator rejects non-numeric).
        cached_perf_id = str(snowflake_id())
        cached_performer = PerformerFactory.build(
            id=cached_perf_id,
            name="DifferentName",
            alias_list=[],
            urls=[fansly_url],
        )
        await respx_stash_processor.context.get_client()
        respx_stash_processor.context.store.add(cached_performer)

        # Production tries name + alias GraphQL queries before reaching the
        # URL cache filter. Mock those as empty findPerformers so we fall
        # through to the URL-cache-hit branch (lines 153-154) without an
        # extra findPerformers-by-URL call (since cache hits before fallback).
        respx.reset()
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findPerformers by name → empty
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers by alias → empty
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # No third call expected — URL cache hits before find_one fires.
            ]
        )

        try:
            result = await respx_stash_processor._get_or_create_performer(account)
        finally:
            dump_graphql_calls(graphql_route.calls, "test_url_cache_hit")

        # Found via URL cache. Exactly 2 GraphQL calls fired (name + alias);
        # the URL find_one was skipped because the cache filter returned the
        # preloaded performer at lines 153-154.
        assert result.id == cached_perf_id
        assert graphql_route.call_count == 2

    @pytest.mark.asyncio
    async def test_update_performer_avatar_success_logs_avatar_updated(
        self, respx_stash_processor
    ):
        """Line 273: _update_performer_avatar happy path → debug_print 'avatar_updated'.

        The pre-existing tests take the no_avatar_found branch because
        their tempfile has a random name that doesn't contain the avatar's
        local_filename. This test crafts the visual_files path to include
        the filename, so the cache-filter match succeeds and update_avatar
        runs to completion.
        """
        acct_id = snowflake_id()
        username = "avatar_test_user"
        account = AccountFactory.build(id=acct_id, username=username)

        # Avatar with a known filename. The image's visual_files path MUST
        # contain this filename for the cache filter to match.
        local_filename = "specific_avatar_xyz.jpg"
        avatar = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            local_filename=local_filename,
        )
        account.avatar = avatar

        # image_path="default=true" routes the production path through the
        # avatar-update branch rather than skip.
        mock_performer = PerformerFactory.build(
            id="123",
            name=username,
            image_path="default=true",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            avatar_full_path = Path(tmpdir) / local_filename
            Image.new("RGB", (2, 2), color="red").save(avatar_full_path, "JPEG")

            await respx_stash_processor.context.get_client()

            cached_image_dict = create_image_dict(
                id=str(snowflake_id()),
                title=None,
                visual_files=[
                    {
                        "id": str(snowflake_id()),
                        "path": str(avatar_full_path),
                        "basename": local_filename,
                        "parent_folder_id": str(snowflake_id()),
                        "mod_time": "2024-01-01T00:00:00Z",
                        "size": 1024,
                        "fingerprints": [],
                        "width": 2,
                        "height": 2,
                    }
                ],
            )
            cached_image = SGCImage.model_validate(cached_image_dict)
            respx_stash_processor.context.store.add(cached_image)

            performer_dict = create_performer_dict(id="123", name=username)

            # Only performerUpdate should fire — cache hit on findImages.
            graphql_route = respx.post("http://localhost:9999/graphql").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json=create_graphql_response("performerUpdate", performer_dict),
                    ),
                ]
            )

            try:
                # Should hit line 273 debug_print on success.
                await respx_stash_processor._update_performer_avatar(
                    account, mock_performer
                )
            finally:
                dump_graphql_calls(graphql_route.calls, "test_avatar_updated")

            # Exactly one GraphQL call (performerUpdate). The findImages was
            # served from cache via store.filter().
            assert graphql_route.call_count == 1
