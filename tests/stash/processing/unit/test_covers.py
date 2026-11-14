"""Cover images for video-only galleries: which posts get one, how the file is
made, and how it reaches the gallery."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image as PILImage
from stash_graphql_client.types import Image, ImageFile, Scene, VideoFile

from config.modes import DownloadMode
from fileio.normalize import COVERS_DIR, cover_name
from metadata import ContentType, Media, Message, Post
from pathio import get_stash_path
from tests.fixtures.api import dump_fansly_calls, make_synthetic_ivs_segment
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    MediaLocationFactory,
    PostFactory,
)
from tests.fixtures.stash import find_files_response
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_gallery_dict,
    create_graphql_response,
    create_image_dict,
    create_image_file_dict,
    create_scene_dict,
    create_video_file_dict,
)
from tests.fixtures.stash.stash_type_factories import (
    PerformerFactory,
    StudioFactory,
    TagFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"
_POSTER_URL = "https://cdn.fansly.com/content/poster.jpeg"


@pytest.mark.asyncio
async def test_covers_wanted_only_for_items_with_videos_and_no_image(
    respx_stash_processor,
):
    """A cover comes from the first video of each post that has no image.

    A post with an image of its own needs none, nor does one with no video,
    and two video-only posts sharing their first video share its cover. With
    ``stash_context.gallery_covers`` off no post gets one.
    """
    video_only, shares_video, mixed, images_only, audio_only = (
        PostFactory.build(id=snowflake_id()) for _ in range(5)
    )
    unsaved = Post.model_construct(id=None)
    sound = MediaFactory.build(
        id=snowflake_id(), mimetype="audio/mp4", local_filename="a.mp4"
    )
    first, second, other = sorted(
        (
            MediaFactory.build(
                id=snowflake_id(), mimetype="video/mp4", local_filename=f"v{n}.mp4"
            )
            for n in range(3)
        ),
        key=lambda m: m.id,
    )
    picture = MediaFactory.build(
        id=snowflake_id(), mimetype="image/jpeg", local_filename="p.jpeg"
    )
    index = {
        "v0.mp4": (first, [video_only, shares_video, unsaved]),
        "v1.mp4": (second, [video_only]),
        "v2.mp4": (other, [mixed]),
        "p.jpeg": (picture, [mixed, images_only]),
        "a.mp4": (sound, [audio_only]),
    }

    wanted = respx_stash_processor._covers_wanted(index)
    respx_stash_processor.config.stash_gallery_covers = False
    switched_off = respx_stash_processor._covers_wanted(index)

    assert wanted == {
        cover_name(first.local_filename): (
            first,
            {video_only.id: video_only, shares_video.id: shares_video},
        )
    }
    assert switched_off == {}


@pytest.mark.asyncio
async def test_ensure_covers_takes_the_first_frame_once(
    respx_stash_processor, tmp_path, caplog
):
    """A missing cover is the video's first frame; an existing one is kept.

    A video that cannot be read or is not on this computer gets no cover.
    """
    caplog.set_level(logging.WARNING)
    processor = respx_stash_processor
    videos = tmp_path / "creator" / "Timeline" / "Videos"
    videos.mkdir(parents=True)
    good_path = videos / "2026-01-02_at_03-04_UTC_id_1.mp4"
    good_path.write_bytes(make_synthetic_ivs_segment())
    broken_path = videos / "2026-01-02_at_03-04_UTC_id_2.mp4"
    broken_path.write_bytes(b"not a video")
    good, broken, absent, elsewhere = (
        MediaFactory.build(id=snowflake_id(), mimetype="video/mp4", local_filename=name)
        for name in (good_path.name, broken_path.name, "absent.mp4", "elsewhere.mp4")
    )
    wanted: dict[str, tuple[Media, dict[int, Post | Message]]] = {
        cover_name(media.local_filename): (media, {})
        for media in (good, broken, absent, elsewhere)
    }
    locations = {
        good_path.name: good_path,
        broken_path.name: broken_path,
        "absent.mp4": videos / "absent.mp4",
    }

    created = await processor._ensure_covers(wanted, locations)
    again = await processor._ensure_covers(wanted, locations)

    cover = videos / COVERS_DIR / cover_name(good_path.name)
    assert created == [cover]
    assert again == []
    assert [p.name for p in (videos / COVERS_DIR).iterdir()] == [cover.name]
    with PILImage.open(cover) as image:
        assert (image.format, image.size) == ("JPEG", (320, 240))
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum(f"No cover made for {broken_path.name}" in m for m in warnings) == 2


@pytest.mark.parametrize("case", ["poster", "refused", "stash_only", "no_poster"])
@pytest.mark.asyncio
async def test_new_download_takes_the_fansly_poster(
    case, respx_fansly_api, respx_stash_processor, tmp_path
):
    """A video downloaded in this run gets Fansly's poster as its cover.

    When Fansly refuses it, when the video carries no poster, and in stash-only
    mode (which does not contact Fansly) the cover is the first frame.
    """
    processor = respx_stash_processor
    processor.config._api = respx_fansly_api
    if case == "stash_only":
        processor.config.download_mode = DownloadMode.STASH_ONLY
    videos = tmp_path / "creator" / "Timeline" / "Videos"
    videos.mkdir(parents=True)
    video_path = videos / "2026-01-02_at_03-04_UTC_id_1.mp4"
    video_path.write_bytes(make_synthetic_ivs_segment())
    poster = MediaFactory.build(id=snowflake_id(), mimetype="image/jpeg", type=1)
    poster.locations = [
        MediaLocationFactory.build(
            mediaId=poster.id, locationId=1, location=_POSTER_URL, raw_url=_POSTER_URL
        )
    ]
    video = MediaFactory.build(
        id=snowflake_id(), mimetype="video/mp4", local_filename=video_path.name
    )
    video.local_path = str(video_path)
    video.variants = [] if case == "no_poster" else [poster]
    answers = {
        "poster": [httpx.Response(200, content=b"poster bytes")],
        "refused": [httpx.Response(403, content=b"expired")],
    }.get(case, [])
    cdn_route = respx.get(url__startswith=_POSTER_URL).mock(side_effect=answers)

    try:
        created = await processor._ensure_covers(
            {cover_name(video_path.name): (video, {})}, {}
        )
    finally:
        dump_fansly_calls(cdn_route.calls, f"cover_{case}")

    cover = videos / COVERS_DIR / cover_name(video_path.name)
    assert created == [cover]
    assert cdn_route.call_count == len(answers)
    if case == "poster":
        assert cover.read_bytes() == b"poster bytes"
    else:
        with PILImage.open(cover) as image:
            assert image.format == "JPEG"


@pytest.mark.parametrize("case", ["already_scanned", "made_this_run", "daemon_pass"])
@pytest.mark.asyncio
async def test_run_file_first_gives_a_video_only_gallery_its_cover(
    case, entity_store, respx_stash_processor
):
    """An already-synced video-only post gets its cover image and the cover set.

    The cover is either in Stash already but in no gallery, or made in this
    run from the video on disk and scanned before it is looked up; the
    daemon's pass does the same for a video it just downloaded. The run tags
    the image, adds it to the post's gallery and then sets it as that
    gallery's cover. A later run sees the image in a gallery and writes
    nothing.
    """
    processor = respx_stash_processor
    processor.config.stash_scan_settle_s = 0.0
    made_now = case != "already_scanned"
    root = get_stash_path(processor.state.base_path, processor.config).rstrip("/")
    acct_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    await entity_store.save(account)
    video_name = "2024-01-01_at_00-00_UTC_id_1.mp4"
    video_path = f"{root}/test_user/{video_name}"
    cover_path = f"{root}/test_user/{COVERS_DIR}/{cover_name(video_name)}"
    media = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename=video_name,
        stash_id=700,
    )
    await entity_store.save(media)
    await entity_store.save(
        AccountMediaFactory.build(id=media.id, accountId=acct_id, mediaId=media.id)
    )
    post = PostFactory.build(id=snowflake_id(), accountId=acct_id, stash_id=5000)
    await entity_store.save(post)
    await entity_store.save(
        AttachmentFactory.build(
            id=snowflake_id(),
            postId=post.id,
            contentId=media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    )
    processor._account = account
    processor._performer = PerformerFactory(
        id="123", name="test_user", scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name="test_user (Fansly)")
    processor.store.add(TagFactory(id="900", name="Cover"))

    video_file = create_video_file_dict("20", video_path)
    scene = create_scene_dict(
        id="700",
        title="Done",
        code=str(media.id),
        date="2024-01-01",
        details="synced",
        urls=[f"https://fansly.com/post/{post.id}"],
        files=[video_file],
    )
    cover_file = create_image_file_dict("21", cover_path)
    gallery = create_gallery_dict(
        id="5000", title="Done", code=str(post.id), date="2024-01-01"
    )

    video_swept = {**video_file, "scenes": [scene]}

    def cover_swept(image_galleries: list[dict]) -> dict:
        image = create_image_dict(
            id="800", visual_files=[cover_file], galleries=image_galleries
        )
        return {**cover_file, "images": [image]}

    scan = [
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, json={"data": {"metadataScan": "job_1"}}),
        httpx.Response(
            200,
            json=create_graphql_response(
                "findJob", {"id": "job_1", "status": "FINISHED"}
            ),
        ),
    ]
    if made_now:
        local_video: Path = processor.state.base_path / "test_user" / video_name
        await asyncio.to_thread(local_video.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(local_video.write_bytes, make_synthetic_ivs_segment())
    if case == "daemon_pass":
        # The video was downloaded in this run; it and its new cover are
        # scanned together, then each is looked up by name.
        media.local_path = str(local_video)
        found = [
            *scan,
            # No sweep here: the synced video's scene is re-read by id, which
            # settles the video, so only the cover is looked up by name.
            httpx.Response(200, json=create_graphql_response("findScene", scene)),
            find_files_response(cover_swept([])),
        ]
    elif case == "made_this_run":
        # The sweep has no cover yet; it is made, scanned, then looked up.
        found = [
            find_files_response(video_swept),
            *scan,
            find_files_response(cover_swept([])),
        ]
    else:
        found = [find_files_response(video_swept, cover_swept([]))]

    def galleries() -> httpx.Response:
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findGalleries", {"count": 1, "galleries": [gallery]}
            ),
        )

    answers = [
        *found,
        # The unlinked cover image is re-read once, complete.
        httpx.Response(
            200,
            json=create_graphql_response(
                "findImage",
                create_image_dict(id="800", visual_files=[cover_file]),
            ),
        ),
        galleries(),
        galleries(),
        galleries(),
        httpx.Response(200, json={"data": {"op0": {"id": "800"}}}),
        httpx.Response(200, json={"data": {"addGalleryImages": True}}),
        httpx.Response(200, json={"data": {"setGalleryCover": True}}),
        find_files_response(video_swept, cover_swept([{"id": "5000"}])),
    ]
    route = respx.post(_GRAPHQL_URL).mock(side_effect=answers)
    first_run = (
        processor._run_file_first_incremental
        if case == "daemon_pass"
        else processor._run_file_first
    )
    try:
        await first_run(account, processor._performer, studio)
        first_run_calls = route.call_count
        media.local_path = None
        await processor._run_file_first(account, processor._performer, studio)
    finally:
        dump_graphql_calls(route.calls, f"video_only_gallery_cover_{case}")

    requests = [json.loads(call.request.content) for call in route.calls]
    cover_calls = [r for r in requests if "setGalleryCover" in r["query"]]
    stamped = next(r for r in requests if "ImageUpdateInput" in r["query"])
    added = next(r for r in requests if "AddGalleryImages" in r["query"])
    assert first_run_calls == len(answers) - 1
    assert route.call_count == len(answers)
    assert stamped["variables"]["input0"]["id"] == "800"
    assert stamped["variables"]["input0"]["tag_ids"] == ["900"]
    assert stamped["variables"]["input0"]["performer_ids"] == ["123"]
    assert stamped["variables"]["input0"]["studio_id"] == "200"
    assert added["variables"]["input"] == {"gallery_id": "5000", "image_ids": ["800"]}
    if made_now:
        with PILImage.open(cover_path) as image:
            assert image.format == "JPEG"
    assert [r["variables"]["input"] for r in cover_calls] == [
        {"gallery_id": "5000", "cover_image_id": "800"}
    ]


@pytest.mark.asyncio
async def test_link_covers_takes_only_an_unlinked_cover(respx_stash_processor):
    """Only a wanted image in a Covers folder that is in no gallery is linked.

    A same-named image outside a Covers folder is not a cover, one Stash has no
    image for cannot be linked, and one that turns out to be in a gallery once
    re-read was linked before. Where the mapped root replaces the download
    directory a video's local path is unknown, so no cover is made from it.
    """
    processor = respx_stash_processor
    post = PostFactory.build(id=snowflake_id())
    video = MediaFactory.build(
        id=snowflake_id(), mimetype="video/mp4", local_filename="v_id_1.mp4"
    )
    wanted: dict[str, tuple[Media, dict[int, Post | Message]]] = {
        "v_id_1.jpeg": (video, {post.id: post})
    }
    cover_path = f"/dl/creator/{COVERS_DIR}/v_id_1.jpeg"
    files = [
        ImageFile(id="31", path="/dl/creator/Pictures/v_id_1.jpeg", images=[]),
        ImageFile(id="32", path=cover_path, images=[]),
        ImageFile(id="33", path=cover_path, images=[Image(id="801")]),
    ]
    item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]] = {}
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findImage",
                    create_image_dict(
                        id="801",
                        visual_files=[create_image_file_dict("33", cover_path)],
                        galleries=[{"id": "5000"}],
                    ),
                ),
            )
        ]
    )
    account = AccountFactory.build(id=snowflake_id(), username="creator")

    try:
        await processor._link_covers(files, wanted, account, None, item_entities)
    finally:
        dump_graphql_calls(route.calls, "link_covers_skips")
    video_file = VideoFile(id="20", path="/stash/lib/v_id_1.mp4")
    direct = processor._local_video_paths([video_file])
    processor.config.stash_mapped_path = Path("/stash/lib")
    processor.config.stash_override_dldir_w_mapped = True
    overridden = processor._local_video_paths([video_file])

    assert route.call_count == 1
    assert item_entities == {}
    assert processor._covers == {}
    assert direct == {"v_id_1.mp4": Path("/stash/lib/v_id_1.mp4")}
    assert overridden == {}


@pytest.mark.asyncio
async def test_cover_is_not_set_for_an_item_without_a_gallery(
    entity_store, respx_stash_processor
):
    """A cover whose item ended up with no gallery is dropped, not sent."""
    processor = respx_stash_processor
    post = PostFactory.build(id=snowflake_id())
    processor._covers = {post.id: Image(id="801")}

    def no_galleries() -> httpx.Response:
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findGalleries", {"count": 0, "galleries": []}
            ),
        )

    route = respx.post(_GRAPHQL_URL).mock(side_effect=[no_galleries(), no_galleries()])
    account = AccountFactory.build(id=snowflake_id(), username="creator")
    performer = PerformerFactory(
        id="123", name="creator", scenes=[], images=[], galleries=[]
    )

    try:
        await processor._compose_and_flush(
            account, performer, None, {post.id: (post, [])}, [], []
        )
    finally:
        dump_graphql_calls(route.calls, "cover_without_gallery")

    requests = [json.loads(call.request.content) for call in route.calls]
    assert route.call_count == 2
    assert not any("SetGalleryCover" in r["query"] for r in requests)
    assert processor._covers == {}
