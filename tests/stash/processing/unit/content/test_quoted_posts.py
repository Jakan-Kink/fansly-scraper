"""Quoted posts: media reached through a quote belongs to the original post.

A quote is a wrapper post by one creator around another creator's post. The
media index files quoted media under the original post first, and stamping
credits it to the original post's author, never to the quoter.
"""

import io
import json

import httpx
import pytest
import respx
from loguru import logger as loguru_logger
from stash_graphql_client.types import ImageFile, Scene, Studio

from metadata import PostMention
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    MediaFactory,
    MessageFactory,
    PostFactory,
)
from tests.fixtures.stash import find_files_response, seed_processor_caches
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_performers_result,
    create_gallery_dict,
    create_graphql_response,
    create_performer_dict,
    create_video_file_dict,
)
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"


@pytest.mark.asyncio
async def test_quoted_media_is_indexed_under_the_original_post(
    respx_stash_processor, quoted_post_setup
):
    """The index lists the original post first, then the quoting post."""
    setup = quoted_post_setup
    original, wrapper = setup["original"], setup["wrapper"]
    quoted_media, own_media = setup["quoted_media"], setup["own_media"]

    pairs = await respx_stash_processor._collect_owned_media(wrapper)
    index = await respx_stash_processor._build_media_index([wrapper])

    assert [(media.id, owner.id) for media, owner in pairs] == [
        (quoted_media.id, original.id),
        (own_media.id, wrapper.id),
    ]
    assert {
        leaf: (media.id, [owner.id for owner in owners])
        for leaf, (media, owners) in index.items()
    } == {
        quoted_media.local_filename: (quoted_media.id, [original.id, wrapper.id]),
        own_media.local_filename: (own_media.id, [wrapper.id]),
    }


@pytest.mark.asyncio
async def test_quoted_media_is_stamped_from_the_original_poster(
    respx_stash_processor, quoted_post_setup
):
    """Text, date, link, performer and studio come from the original post.

    The quoter is the creator being processed; the scene still gets the
    original post's text and link, its author's performer (looked up by Fansly
    URL) and studio, and the author's account records the performer id. The
    scene starts as an earlier version left it, stamped from the quoter: that
    credit is taken off again, and a link that is not a Fansly post stays.
    """
    setup = quoted_post_setup
    quoter, author, wrapper = setup["quoter"], setup["author"], setup["wrapper"]
    original, quoted_media = setup["original"], setup["quoted_media"]
    processor = respx_stash_processor
    quoter_studio = seed_processor_caches(processor, quoter)
    processor.store.add(StudioFactory(id="10", name="Fansly (network)"))
    processor.store.add(StudioFactory(id="20", name=f"{author.username} (Fansly)"))
    author_url = f"https://fansly.com/{author.username}"
    processor.store.add(
        PerformerFactory(
            id="321", name=author.username, urls=[author_url], scenes=[], images=[]
        )
    )
    scene = Scene(
        id="700",
        title=wrapper.content,
        details=wrapper.content,
        date="2024-03-06",
        code=str(quoted_media.id),
        performers=[processor._performer],
        studio=quoter_studio,
        tags=[],
        urls=["https://example.com/kept", f"https://fansly.com/post/{wrapper.id}"],
    )

    def _author_performer() -> httpx.Response:
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findPerformers",
                create_find_performers_result(
                    count=1,
                    performers=[
                        create_performer_dict(
                            id="321", name=author.username, urls=[author_url]
                        )
                    ],
                ),
            ),
        )

    # The URL lookup asks for the count, then for the page.
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[_author_performer(), _author_performer()]
    )
    try:
        await processor._stamp_metadata(
            scene, quoted_media, original, quoter, quoter_studio
        )
    finally:
        dump_graphql_calls(route.calls, "quoted_media_stamped_from_original_poster")

    assert route.call_count == 2
    assert author_url.removeprefix("https://") in json.dumps(
        json.loads(route.calls[0].request.content)["variables"]
    )
    assert scene.code == str(quoted_media.id)
    assert scene.details == original.content
    assert scene.date == "2024-03-05"
    assert scene.title == original.content
    assert scene.urls == [
        "https://example.com/kept",
        f"https://fansly.com/post/{original.id}",
    ]
    assert isinstance(scene.performers, list)
    assert [performer.id for performer in scene.performers] == ["321"]
    assert isinstance(scene.studio, Studio)
    assert scene.studio.id == "20"
    assert author.stash_id == 321


@pytest.mark.asyncio
async def test_quoter_credit_stays_when_organized_or_mentioned(
    respx_stash_processor, quoted_post_setup
):
    """An organized object is not touched, and a mentioned quoter stays a performer.

    No route is registered: the organized scene must cause no Stash request.
    """
    setup = quoted_post_setup
    quoter, wrapper = setup["quoter"], setup["wrapper"]
    original, quoted_media = setup["original"], setup["quoted_media"]
    processor = respx_stash_processor
    quoter_studio = seed_processor_caches(processor, quoter)
    wrapper_url = f"https://fansly.com/post/{wrapper.id}"
    organized = Scene(
        id="710",
        title=wrapper.content,
        code=str(quoted_media.id),
        organized=True,
        performers=[processor._performer],
        tags=[],
        urls=[wrapper_url],
    )
    mentioning = PostFactory.build(id=snowflake_id(), accountId=snowflake_id())
    mentioning.mentions = [
        PostMention(postId=mentioning.id, accountId=quoter.id, handle="quoter")
    ]
    mentioned = Scene(
        id="711", performers=[processor._performer], tags=[], urls=[wrapper_url]
    )

    await processor._stamp_metadata(
        organized, quoted_media, original, quoter, quoter_studio
    )
    processor._drop_quoter_credit(mentioned, mentioning, quoter)

    assert organized.title == wrapper.content
    assert organized.urls == [wrapper_url]
    assert isinstance(organized.performers, list)
    assert isinstance(mentioned.performers, list)
    assert [performer.id for performer in organized.performers] == ["123"]
    assert [performer.id for performer in mentioned.performers] == ["123"]
    assert mentioned.urls == []


@pytest.mark.asyncio
async def test_quoted_file_outside_the_creator_folder_is_looked_up_by_name(
    respx_stash_processor, quoted_post_setup
):
    """Quoted media the creator sweep did not return is fetched by file name.

    The quoter's own image was swept; the quoted video lives under its original
    poster, so one ``findFiles`` by basename brings it in.
    """
    setup = quoted_post_setup
    quoter, wrapper = setup["quoter"], setup["wrapper"]
    quoted_media, own_media = setup["quoted_media"], setup["own_media"]
    processor = respx_stash_processor
    index = await processor._build_media_index([wrapper])
    swept = [ImageFile(id="1", path=f"/dl/quoter/{own_media.local_filename}")]
    elsewhere = create_video_file_dict(
        "2", f"/dl/original_poster/{quoted_media.local_filename}"
    )

    route = respx.post(_GRAPHQL_URL).mock(side_effect=[find_files_response(elsewhere)])
    try:
        found = await processor._quoted_files_elsewhere(index, swept, quoter)
        nothing_missing = await processor._quoted_files_elsewhere(
            index, [*swept, *found], quoter
        )
    finally:
        dump_graphql_calls(route.calls, "quoted_file_looked_up_by_name")

    assert route.call_count == 1
    assert str(quoted_media.id) in json.dumps(
        json.loads(route.calls[0].request.content)["variables"]
    )
    assert [file.id for file in found] == ["2"]
    assert nothing_missing == []


@pytest.mark.asyncio
async def test_preview_in_a_post_and_a_message_takes_the_post(
    entity_store, respx_stash_processor
):
    """A preview's canonical owner is the post, even when the message is older.

    The full media keeps the earliest owner first.
    """
    account = AccountFactory.build(id=snowflake_id(), username="test_user")
    main_id, preview_id, message_id = snowflake_id(), snowflake_id(), snowflake_id()
    post_id = message_id + 1000
    main = MediaFactory.build(
        id=main_id,
        accountId=account.id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename=f"2025-03-12_at_16-02_UTC_id_{main_id}.mp4",
    )
    preview = MediaFactory.build(
        id=preview_id,
        accountId=account.id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename=f"2025-03-12_at_16-02_UTC_preview_id_{preview_id}.mp4",
    )
    attachment = {"pos": 0, "contentType": 1, "contentId": main_id}
    message = MessageFactory.build(
        id=message_id,
        groupId=None,
        senderId=account.id,
        attachments=[{**attachment, "messageId": message_id}],
    )
    post = PostFactory.build(
        id=post_id,
        accountId=account.id,
        attachments=[{**attachment, "postId": post_id}],
    )
    for entity in (account, main, preview):
        await entity_store.save(entity)
    await entity_store.save(
        AccountMediaFactory.build(
            id=main_id, accountId=account.id, mediaId=main_id, previewId=preview_id
        )
    )
    for item in (message, post):
        await entity_store.save(item)

    index = await respx_stash_processor._build_media_index([message, post])

    assert {
        leaf: [type(owner).__name__ for owner in owners]
        for leaf, (_media, owners) in index.items()
    } == {
        main.local_filename: ["Message", "Post"],
        preview.local_filename: ["Post", "Message"],
    }


@pytest.mark.asyncio
async def test_post_by_an_unknown_account_is_left_unstamped(
    respx_stash_processor, quoted_post_setup
):
    """Without the author's account there is nobody to credit, so nothing is stamped."""
    quoter = quoted_post_setup["quoter"]
    quoted_media = quoted_post_setup["quoted_media"]
    orphan = PostFactory.build(
        id=snowflake_id(), accountId=snowflake_id(), content="Author never seen"
    )
    studio = seed_processor_caches(respx_stash_processor, quoter)
    scene = Scene(id="701", title="", performers=[], tags=[], urls=[])

    sink = io.StringIO()
    sink_id = loguru_logger.add(sink, level="WARNING")
    try:
        await respx_stash_processor._stamp_metadata(
            scene, quoted_media, orphan, quoter, studio
        )
        # No gallery either: any Stash request here would fail as unmocked.
        await respx_stash_processor._compose_gallery_for_item(
            orphan, [scene], quoter, respx_stash_processor._performer, studio
        )
    finally:
        loguru_logger.remove(sink_id)

    assert sink.getvalue().count("not in the metadata database") == 2
    assert scene.title == ""
    assert scene.performers == []


@pytest.mark.asyncio
async def test_original_posts_gallery_is_created_for_its_author(
    respx_stash_processor, quoted_post_setup
):
    """The quoter's pass creates the original post's gallery under the author."""
    setup = quoted_post_setup
    quoter, author, original = setup["quoter"], setup["author"], setup["original"]
    processor = respx_stash_processor
    quoter_studio = seed_processor_caches(processor, quoter)
    processor.store.add(StudioFactory(id="10", name="Fansly (network)"))
    processor.store.add(StudioFactory(id="20", name=f"{author.username} (Fansly)"))
    author_url = f"https://fansly.com/{author.username}"
    processor.store.add(
        PerformerFactory(
            id="321", name=author.username, urls=[author_url], galleries=[]
        )
    )
    # Lookups that miss the store mean "no gallery yet", as after the preload.
    processor._galleries_preloaded = True

    def _author_performer() -> httpx.Response:
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findPerformers",
                create_find_performers_result(
                    count=1,
                    performers=[
                        create_performer_dict(
                            id="321", name=author.username, urls=[author_url]
                        )
                    ],
                ),
            ),
        )

    # The URL lookup asks for the count, then for the page; then the create.
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            _author_performer(),
            _author_performer(),
            httpx.Response(
                200,
                json=create_graphql_response(
                    "galleryCreate",
                    create_gallery_dict(id="900", code=str(original.id)),
                ),
            ),
        ]
    )
    try:
        await processor._compose_gallery_for_item(
            original, [], quoter, processor._performer, quoter_studio
        )
    finally:
        dump_graphql_calls(route.calls, "original_posts_gallery_for_its_author")

    created = [
        json.loads(call.request.content)["variables"]["input"]
        for call in route.calls
        if b"galleryCreate" in call.request.content
    ]
    assert len(created) == 1
    assert created[0]["code"] == str(original.id)
    assert created[0]["title"] == original.content
    assert created[0]["performer_ids"] == ["321"]
    assert created[0]["studio_id"] == "20"
    assert created[0]["urls"] == [f"https://fansly.com/post/{original.id}"]
    assert original.stash_id == 900
