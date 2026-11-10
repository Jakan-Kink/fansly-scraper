"""Performer matching: Fansly URL, then alias, then name.

Drives the real AccountProcessingMixin lookups over the respx Stash edge.
``store.find`` issues a count query first and a second fetch only when the
count is non-zero, so a hit costs two findPerformers calls and a miss one.
"""

import json
import logging

import httpx
import pytest
import respx

from metadata import PostMention
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_graphql_response,
    create_performer_dict,
)
from tests.fixtures.utils.test_isolation import snowflake_id


GRAPHQL_URL = "http://localhost:9999/graphql"
NONE_FOUND = create_graphql_response("findPerformers", {"count": 0, "performers": []})


@pytest.mark.asyncio
async def test_matches_fansly_url_ignoring_case_and_path_suffix(respx_stash_processor):
    """A curated performer is found by its Fansly URL despite case and ``/posts``."""
    account = AccountFactory.build(
        id=snowflake_id(), username="somecreator", displayName="Some Display"
    )
    found = create_graphql_response(
        "findPerformers",
        {
            "count": 1,
            "performers": [
                create_performer_dict(
                    id="888",
                    name="Curated Name",
                    urls=["https://fansly.com/SomeCreator/posts"],
                )
            ],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=found), httpx.Response(200, json=found)]
    )

    try:
        performer = await respx_stash_processor._get_or_create_performer(account)
        again = await respx_stash_processor._get_or_create_performer(account)
    finally:
        dump_graphql_calls(route.calls, "test_matches_fansly_url")

    assert performer is not None
    assert performer.id == "888"
    assert again is performer
    # Count + fetch for the URL search; the repeat lookup is served from memory.
    assert route.call_count == 2
    first_body = route.calls[0].request.content.decode()
    assert "findPerformers" in first_body
    assert "fansly.com/somecreator" in first_body


@pytest.mark.asyncio
async def test_returns_every_performer_carrying_the_account_url(respx_stash_processor):
    """Both performers of a two-performer account match; lowest id is primary."""
    account = AccountFactory.build(id=snowflake_id(), username="somecouple")
    found = create_graphql_response(
        "findPerformers",
        {
            "count": 2,
            "performers": [
                create_performer_dict(
                    id="1087",
                    name="Second Person",
                    urls=["https://fansly.com/someCouple/posts"],
                ),
                create_performer_dict(
                    id="1086",
                    name="First Person",
                    urls=["https://fansly.com/someCouple/posts"],
                ),
            ],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[httpx.Response(200, json=found), httpx.Response(200, json=found)]
    )

    try:
        matches = await respx_stash_processor._match_performers(account)
        primary = await respx_stash_processor._get_or_create_performer(account)
    finally:
        dump_graphql_calls(route.calls, "test_returns_every_performer")

    assert matches is not None
    assert [p.id for p in matches] == ["1086", "1087"]
    assert primary is not None
    assert primary.id == "1086"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_mention_with_punctuation_is_cleaned_before_match_and_create(
    respx_stash_processor,
):
    """``name!`` searches and creates as ``name``; a longer username is no match."""
    mention = PostMention(
        id=snowflake_id(), postId=snowflake_id(), handle="somecreator!"
    )
    longer_username = create_graphql_response(
        "findPerformers",
        {
            "count": 1,
            "performers": [
                create_performer_dict(
                    id="333",
                    name="Someone Else",
                    urls=["https://fansly.com/somecreator2"],
                )
            ],
        },
    )
    created = create_graphql_response(
        "performerCreate",
        create_performer_dict(
            id="501",
            name="somecreator",
            urls=["https://fansly.com/somecreator"],
            alias_list=["somecreator"],
        ),
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=longer_username),  # URL search: count
            httpx.Response(200, json=longer_username),  # URL search: fetch
            httpx.Response(200, json=NONE_FOUND),  # alias search
            httpx.Response(200, json=NONE_FOUND),  # name search
            httpx.Response(200, json=created),
        ]
    )

    try:
        performer = await respx_stash_processor._get_or_create_performer(mention)
    finally:
        dump_graphql_calls(route.calls, "test_mention_with_punctuation")

    assert performer is not None
    assert performer.id == "501"
    assert route.call_count == 5
    bodies = [call.request.content.decode() for call in route.calls]
    assert all("somecreator!" not in body for body in bodies)
    create_input = json.loads(bodies[4])["variables"]["input"]
    assert create_input["name"] == "somecreator"
    assert create_input["alias_list"] == ["somecreator"]
    assert create_input["urls"] == ["https://fansly.com/somecreator"]


@pytest.mark.asyncio
async def test_matches_alias_ignoring_case(respx_stash_processor):
    """With no URL match, the username matches an alias regardless of case."""
    mention = PostMention(
        id=snowflake_id(), postId=snowflake_id(), handle="somecreator's"
    )
    by_alias = create_graphql_response(
        "findPerformers",
        {
            "count": 1,
            "performers": [
                create_performer_dict(
                    id="777", name="Curated Name", alias_list=["SomeCreator"]
                )
            ],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=NONE_FOUND),  # URL search
            httpx.Response(200, json=by_alias),  # alias search: count
            httpx.Response(200, json=by_alias),  # alias search: fetch
        ]
    )

    try:
        performer = await respx_stash_processor._find_existing_performer(mention)
    finally:
        dump_graphql_calls(route.calls, "test_matches_alias")

    assert performer is not None
    assert performer.id == "777"
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_single_name_match_links(respx_stash_processor):
    """A performer matched by name alone is linked when it is the only one."""
    account = AccountFactory.build(
        id=snowflake_id(), username="somecreator", displayName="Some Display"
    )
    by_name = create_graphql_response(
        "findPerformers",
        {
            "count": 1,
            "performers": [create_performer_dict(id="42", name="Some Display")],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=NONE_FOUND),  # URL search
            httpx.Response(200, json=NONE_FOUND),  # alias search
            httpx.Response(200, json=by_name),  # name search: count
            httpx.Response(200, json=by_name),  # name search: fetch
        ]
    )

    try:
        performer = await respx_stash_processor._get_or_create_performer(account)
    finally:
        dump_graphql_calls(route.calls, "test_single_name_match")

    assert performer is not None
    assert performer.id == "42"
    assert route.call_count == 4
    assert "Some Display" in route.calls[2].request.content.decode()


@pytest.mark.asyncio
async def test_namesakes_without_identifier_link_none_and_create_none(
    respx_stash_processor, caplog
):
    """Several performers share the name and none carries the account: no guess."""
    account = AccountFactory.build(
        id=snowflake_id(), username="somecreator", displayName="Common Name"
    )
    namesakes = create_graphql_response(
        "findPerformers",
        {
            "count": 2,
            "performers": [
                create_performer_dict(
                    id="10", name="Common Name", disambiguation="One"
                ),
                create_performer_dict(
                    id="11", name="Common Name", disambiguation="Two"
                ),
            ],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=NONE_FOUND),  # URL search
            httpx.Response(200, json=NONE_FOUND),  # alias search
            httpx.Response(200, json=namesakes),  # name search: count
            httpx.Response(200, json=namesakes),  # name search: fetch
        ]
    )

    caplog.set_level(logging.WARNING)
    try:
        performer = await respx_stash_processor._get_or_create_performer(account)
        existing = await respx_stash_processor._find_existing_performer(account)
    finally:
        dump_graphql_calls(route.calls, "test_namesakes")

    assert performer is None
    assert existing is None
    assert route.call_count == 4  # no performerCreate
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("Common Name" in msg and "somecreator" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_handle_with_no_username_matches_and_creates_nothing(
    respx_stash_processor,
):
    """A handle holding no username characters never reaches Stash."""
    mention = PostMention(id=snowflake_id(), postId=snowflake_id(), handle="!!!")
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(side_effect=[])

    try:
        performer = await respx_stash_processor._get_or_create_performer(mention)
    finally:
        dump_graphql_calls(route.calls, "test_handle_with_no_username")

    assert performer is None
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_process_creator_fails_on_unresolvable_namesakes(
    respx_stash_processor, entity_store
):
    """The creator is not processed against a guessed namesake."""
    acct_id = snowflake_id()
    account = AccountFactory.build(
        id=acct_id, username="somecreator", displayName="Common Name", stash_id=None
    )
    await entity_store.save(account)
    respx_stash_processor.state.creator_id = acct_id
    namesakes = create_graphql_response(
        "findPerformers",
        {
            "count": 2,
            "performers": [
                create_performer_dict(
                    id="10", name="Common Name", disambiguation="One"
                ),
                create_performer_dict(
                    id="11", name="Common Name", disambiguation="Two"
                ),
            ],
        },
    )
    await respx_stash_processor.context.get_client()
    route = respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=NONE_FOUND),  # URL search
            httpx.Response(200, json=NONE_FOUND),  # alias search
            httpx.Response(200, json=namesakes),  # name search: count
            httpx.Response(200, json=namesakes),  # name search: fetch
        ]
    )

    try:
        with pytest.raises(ValueError, match=r"fansly\.com/somecreator"):
            await respx_stash_processor.process_creator()
    finally:
        dump_graphql_calls(route.calls, "test_process_creator_namesakes")

    assert route.call_count == 4
