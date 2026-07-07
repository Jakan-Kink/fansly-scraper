"""Tests for tag processing methods in TagProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import httpx
import pytest
import respx
from stash_graphql_client import present

from stash.processing import StashProcessing
from tests.fixtures.metadata import HashtagFactory
from tests.fixtures.stash import (
    SceneFactory,
    TagFactory,
    create_find_scenes_result,
    create_find_tags_result,
    create_graphql_response,
    create_tag_create_result,
    create_tag_dict,
)
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
)


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_empty(respx_stash_processor):
    """Empty input short-circuits at tag.py:93 before any HTTP call."""
    tags = await respx_stash_processor._process_hashtags_to_tags([])

    assert tags == []


@pytest.mark.asyncio
async def test_process_hashtags_sequential_fallback_skips_failing_tag(
    respx_stash_processor, monkeypatch
):
    """tag.py:141-142: a per-tag failure in the sequential fallback is caught.

    Per-task GraphQL errors are swallowed by ``gather(return_exceptions=True)``,
    so the defensive batch→sequential fallback (except at 133) is only reachable
    via a *structural* batch failure. Replacing ``_get_or_create_tag`` with a
    non-coroutine makes ``asyncio.gather`` raise (batch fails), and the
    sequential ``await _get_or_create_tag(...)`` then raises ``TypeError`` too
    (awaiting a non-awaitable) — exercising the inner per-tag except (141-142).
    No HTTP is issued, so no respx route/dump is needed.
    """
    hashtag = HashtagFactory.build(value="boomTag")
    monkeypatch.setattr(
        respx_stash_processor, "_get_or_create_tag", lambda _name: "not-awaitable"
    )

    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Batch failed structurally; the lone sequential tag failed too → none valid.
    assert tags == []


@pytest.mark.parametrize("tag_count", [1, 2], ids=["single_tag", "two_tags"])
@pytest.mark.asyncio
async def test_process_hashtags_to_tags_found(
    respx_stash_processor: StashProcessing,
    request: pytest.FixtureRequest,
    tag_count: int,
) -> None:
    """Processing N found hashtags returns N tags (multiple is N=2 of single).

    Hashtag values are mixed-case (``testTagN``) and the routed tag names are
    lowercase, preserving the case-normalization behavior the original
    single-tag test verified. One findTags call is issued per hashtag.
    """
    hashtags = [HashtagFactory.build(value=f"testTag{i + 1}") for i in range(tag_count)]
    tag_dicts = [
        create_tag_dict(
            id=str(200 + i + 1),
            name=f"testtag{i + 1}",
            aliases=[],
            parents=[],
            children=[],
            description=None,
            image_path=None,
        )
        for i in range(tag_count)
    ]

    # Mock GraphQL responses — one findTags response per hashtag
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findTags", create_find_tags_result(count=1, tags=[tag_dict])
                ),
            )
            for tag_dict in tag_dicts
        ]
    )

    # Test processing hashtags
    try:
        tags = await respx_stash_processor._process_hashtags_to_tags(hashtags)
    finally:
        dump_graphql_calls(graphql_route.calls, request.node.name)

    # Verify all tags were returned, in hashtag order
    assert len(tags) == tag_count
    assert len(graphql_route.calls) == tag_count
    for i, tag in enumerate(tags):
        # Note: Don't assert on ID - library generates UUIDs (implementation detail)
        assert tag.name == f"testtag{i + 1}"
        assert hasattr(tag, "id")  # Verify ID exists


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_not_found_creates_new(respx_stash_processor):
    """Test processing a hashtag that doesn't exist creates a new tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="newTag")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])
    new_tag_dict = create_tag_dict(
        id="200",
        name="newtag",
    )

    # Mock findTags (empty) and tagCreate responses
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First call: findTags by name returns empty
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            # Second call: findTags by alias returns empty
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            # Third call: tagCreate returns new tag
            httpx.Response(
                200,
                json=create_graphql_response(
                    "tagCreate", create_tag_create_result(new_tag_dict)
                ),
            ),
        ]
    )

    # Test processing hashtag
    try:
        tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])
    finally:
        dump_graphql_calls(
            graphql_route.calls, "process_hashtags_to_tags_not_found_creates_new"
        )

    # Verify tag was created and returned
    assert len(tags) == 1
    # Note: Don't assert on ID - library generates UUIDs (implementation detail)
    assert tags[0].name == "newtag"
    assert hasattr(tags[0], "id")  # Verify ID exists


@pytest.mark.asyncio
async def test_add_preview_tag_not_found(respx_stash_processor):
    """Test add_preview_tag when Trailer tag doesn't exist."""
    # Create Scene using factory

    scene = SceneFactory.build(id="300", title="Test Scene", tags=[])

    # Mock empty findTags response
    empty_result = create_find_tags_result(count=0, tags=[])
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            )
        ]
    )

    # Test with scene
    try:
        await respx_stash_processor._add_preview_tag(scene)
    finally:
        dump_graphql_calls(graphql_route.calls, "add_preview_tag_not_found")

    # Verify no tag was added since not found
    assert scene.tags == []


@pytest.mark.asyncio
async def test_add_preview_tag_found_adds_tag(respx_stash_processor):
    """Test add_preview_tag when Trailer tag exists and is added."""
    # Create Scene using factory

    scene = SceneFactory.build(id="300", title="Test Scene", tags=[])

    # Create Trailer tag response
    trailer_tag_dict = create_tag_dict(id="400", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    # _add_preview_tag makes exactly 2 GraphQL calls:
    #   1. findTags — look up the Trailer tag by name
    #   2. findScenes — dedup check: are there scenes that already have the tag?
    empty_scenes = create_find_scenes_result(count=0, scenes=[])
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json=create_graphql_response("findTags", result)),
            httpx.Response(
                200, json=create_graphql_response("findScenes", empty_scenes)
            ),
        ]
    )

    try:
        await respx_stash_processor._add_preview_tag(scene)
    finally:
        dump_graphql_calls(graphql_route.calls, "test_add_preview_tag_found_adds_tag")

    # Verify the tag was added to scene
    scene_tags = present(scene.tags)
    assert len(scene_tags) == 1
    assert scene_tags[0].id == "400"
    assert scene_tags[0].name == "Trailer"

    # Exact count + per-call request + response verification.
    assert len(graphql_route.calls) == 2, (
        f"Expected exactly 2 calls (findTags + findScenes), "
        f"got {len(graphql_route.calls)}"
    )
    # Call 0: findTags filtering on "Trailer"
    assert_op_with_vars(
        graphql_route.calls[0],
        "findTags",
        tag_filter__name__value="Trailer",
    )
    resp0 = graphql_route.calls[0].response.json()
    assert resp0["data"]["findTags"]["count"] == 1
    assert resp0["data"]["findTags"]["tags"][0]["name"] == "Trailer"
    # Call 1: findScenes dedup check (tags.value includes the found tag id)
    assert_op(graphql_route.calls[1], "findScenes")
    resp1 = graphql_route.calls[1].response.json()
    assert resp1["data"]["findScenes"]["count"] == 0


@pytest.mark.asyncio
async def test_add_preview_tag_already_has_tag(respx_stash_processor):
    """Test add_preview_tag when scene already has the Trailer tag."""
    # Create Trailer tag using factory
    trailer_tag = TagFactory.build(id="400", name="Trailer")

    # Create Scene with Trailer tag already added

    scene = SceneFactory.build(
        id="300",
        title="Test Scene",
        tags=[trailer_tag],
    )

    # Create response
    trailer_tag_dict = create_tag_dict(id="400", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", result),
            )
        ]
    )

    # Test with scene
    try:
        await respx_stash_processor._add_preview_tag(scene)
    finally:
        dump_graphql_calls(graphql_route.calls, "add_preview_tag_already_has_tag")

    # Verify the tag was NOT added again (still only one)
    scene_tags = present(scene.tags)
    assert len(scene_tags) == 1
    assert scene_tags[0].id == "400"
