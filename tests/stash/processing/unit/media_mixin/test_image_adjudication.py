"""Unit tests for MediaProcessingMixin image adjudication (Phase 4).

Covers:
- ``_image_files_all_local`` — pure path-ownership logic (no GraphQL).
- The ImageFile branch of ``_process_file_first`` — detect-and-log a foreign
  co-resident (skip, no stamp), stamp a clean all-local image and record
  ``media.stash_id``, and ignore GalleryFile / BasicFile.

We never split or re-own a shared image (Stash offers no clean primary swap for
images); the rule is detect-and-log, mirroring the scene side never re-owning a
shared scene.
"""

import io
from pathlib import PurePath

import httpx
import pytest
import respx
from loguru import logger as loguru_logger
from stash_graphql_client.types import (
    BasicFile,
    GalleryFile,
    Image,
    ImageFile,
)
from stash_graphql_client.types.unset import is_set

from tests.fixtures import (
    create_graphql_response,
    create_image_dict,
    create_image_file_dict,
)
from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash import seed_processor_caches, stash_creator_root
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls


_GRAPHQL_URL = "http://localhost:9999/graphql"


class TestImageFilesAllLocal:
    """Tests for MediaProcessingMixin._image_files_all_local (pure path logic)."""

    def test_all_local_paths_returns_true(self, respx_stash_processor):
        """Every visual file under the creator root → all-local → True."""
        root = stash_creator_root(respx_stash_processor)
        image = Image(
            id="3001",
            visual_files=[
                ImageFile(id="1", path=f"{root}/test_user/x_id_1.jpg"),
                ImageFile(id="2", path=f"{root}/test_user/x_id_2.jpg"),
            ],
        )
        assert respx_stash_processor._image_files_all_local(image) is True

    def test_one_foreign_path_returns_false(self, respx_stash_processor):
        """A single co-resident outside the root → not all-local → False."""
        root = stash_creator_root(respx_stash_processor)
        image = Image(
            id="3002",
            visual_files=[
                ImageFile(id="1", path=f"{root}/test_user/x_id_1.jpg"),
                ImageFile(id="2", path="/other/scraper/foreign.jpg"),
            ],
        )
        assert respx_stash_processor._image_files_all_local(image) is False

    def test_sibling_prefix_path_is_foreign_returns_false(self, respx_stash_processor):
        """A sibling creator sharing a name prefix is NOT local.

        Root ``/dl/anna`` must not claim ``/dl/annabelle/...``: without a
        separator-boundary anchor a bare prefix match treats the sibling's file
        as local, skipping the foreign-coresident guard. The sibling path is
        built by appending to the root with NO separator.
        """
        root = stash_creator_root(respx_stash_processor)
        image = Image(
            id="3006",
            visual_files=[
                ImageFile(id="1", path=f"{root}/test_user/x_id_1.jpg"),
                ImageFile(id="2", path=f"{root}belle/other_user/x_id_2.jpg"),
            ],
        )
        assert respx_stash_processor._image_files_all_local(image) is False

    def test_empty_visual_files_returns_false(self, respx_stash_processor):
        """Empty visual_files → cannot verify ownership → False (skip)."""
        image = Image(id="3003", visual_files=[])
        assert respx_stash_processor._image_files_all_local(image) is False

    def test_unset_visual_files_returns_false(self, respx_stash_processor):
        """UNSET visual_files → cannot verify ownership → False (skip)."""
        image = Image(id="3004")  # visual_files defaults to UNSET
        assert respx_stash_processor._image_files_all_local(image) is False

    def test_mapped_override_local_under_mapped_returns_true(
        self, respx_stash_processor
    ):
        """override + mapped_path: root is the mapped path; under it → True.

        With ``stash_override_dldir_w_mapped`` the root is ``str(mapped_path)``
        regardless of base_path, so a file path under the mapped root is local.
        """
        respx_stash_processor.config.stash_mapped_path = "/stash/library/fansly"
        respx_stash_processor.config.stash_override_dldir_w_mapped = True
        # Sanity: the helper's root is the mapped path, not the tmp base_path.
        assert stash_creator_root(respx_stash_processor) == "/stash/library/fansly"

        image = Image(
            id="3005",
            visual_files=[
                ImageFile(id="1", path="/stash/library/fansly/test_user/x_id_1.jpg"),
            ],
        )
        assert respx_stash_processor._image_files_all_local(image) is True

    def test_mapped_override_foreign_returns_false(self, respx_stash_processor):
        """override + mapped_path: a path outside the mapped root → False."""
        respx_stash_processor.config.stash_mapped_path = "/stash/library/fansly"
        respx_stash_processor.config.stash_override_dldir_w_mapped = True

        image = Image(
            id="3006",
            visual_files=[
                ImageFile(id="1", path="/stash/library/fansly/test_user/x_id_1.jpg"),
                ImageFile(id="2", path="/some/other/mount/x_id_2.jpg"),
            ],
        )
        assert respx_stash_processor._image_files_all_local(image) is False


class TestProcessFileFirstImage:
    """Tests for the ImageFile branch of _process_file_first.

    The dispatcher re-fetches each Image (``invalidate`` + ``get_many``, a real
    ``findImage`` routed over respx) to resolve ``visual_files`` paths before the
    ownership check.
    """

    @pytest.mark.asyncio
    async def test_image_all_local_stamps_and_sets_stash_id(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """All-local image → re-fetch, stamp in place + record media.stash_id.

        The dispatcher re-fetches the image via ``get_many(Image, [id])`` (a real
        ``findImages`` query). Route it to return a path-resolved image so the
        REAL store parses and returns it; assert the re-fetch fired, the fresh
        image was stamped, and ``media.stash_id`` is the int of the image id.
        """
        root = stash_creator_root(respx_stash_processor)
        our = ImageFile(id="800", path=f"{root}/test_user/x_id_42.jpg")
        our.images = [Image(id="900")]  # supplies the loop id; re-fetch resolves it

        # Real re-fetch: get_many → Image.find_by_id issues findImage(id:); route
        # it to return the image with a path-resolved visual file under our root
        # (all-local → owned).
        image_data = create_image_dict(
            id="900",
            title="Test Image",
            performers=[],
            visual_files=[
                create_image_file_dict("800", f"{root}/test_user/x_id_42.jpg")
            ],
        )
        # One call: the re-fetch findImage (the stamp fires no incidental query).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_data)
                )
            ]
        )

        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.jpg")
        studio = seed_processor_caches(respx_stash_processor, mock_account)
        index = {PurePath(our.path).name: (media, [mock_item])}

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "image_all_local_stamps")

        # The fresh (re-fetched) image is returned and was stamped in place.
        assert len(result) == 1
        stamped = result[0]
        assert stamped.id == "900"
        assert stamped.code == str(media.id)
        assert stamped.title
        assert stamped.details == mock_item.content
        # media.stash_id recorded as the int of the owned image id.
        assert media.stash_id == int(stamped.id)
        assert media.stash_id == 900
        # The re-fetch fired: a findImage query was issued for the image.
        assert any(b"findImage" in c.request.content for c in route.calls)

    @pytest.mark.asyncio
    async def test_image_foreign_coresident_logs_and_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """A foreign co-resident → logger.error + NO stamp, NO stash_id.

        Detect-and-log: we don't own a shared image. Assert the error fired
        (loguru sink), no image was stamped, and ``media.stash_id`` was left
        unchanged.
        """
        root = stash_creator_root(respx_stash_processor)
        our = ImageFile(id="801", path=f"{root}/test_user/x_id_42.jpg")
        our.images = [Image(id="901")]  # supplies the loop id; re-fetch resolves it

        # Real re-fetch: findImage returns a SHARED image — one visual file under
        # our root, one foreign — so real _image_files_all_local → False.
        image_data = create_image_dict(
            id="901",
            title="Shared",
            visual_files=[
                create_image_file_dict("801", f"{root}/test_user/x_id_42.jpg"),
                create_image_file_dict("802", "/other/scraper/foreign.jpg"),
            ],
        )
        # One call: the re-fetch findImage (the stamp fires no incidental query).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_data)
                )
            ]
        )
        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.jpg")
        original_stash_id = media.stash_id
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        index = {PurePath(our.path).name: (media, [mock_item])}

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="ERROR")
        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)
            dump_graphql_calls(route.calls, "image_foreign_coresident")

        output = sink.getvalue()
        # Foreign co-resident → not adjudicated into anything → empty list.
        assert result == []
        # The detect-and-log error fired, naming the image and the foreign path.
        assert "foreign co-resident" in output
        assert "901" in output
        assert "/other/scraper/foreign.jpg" in output
        # No stamp: stash_id unchanged.
        assert media.stash_id == original_stash_id
        assert media.stash_id is None

    @pytest.mark.asyncio
    async def test_gallery_file_is_ignored(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """A GalleryFile falls through both branches → no stamp, no error."""
        root = stash_creator_root(respx_stash_processor)
        gfile = GalleryFile(id="803", path=f"{root}/test_user/x_id_42.jpg")
        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.jpg")
        original_stash_id = media.stash_id
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        index = {PurePath(gfile.path).name: (media, [mock_item])}

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="ERROR")
        try:
            result = await respx_stash_processor._process_file_first(
                gfile, index, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)

        assert result == []
        assert media.stash_id == original_stash_id
        assert "foreign co-resident" not in sink.getvalue()

    @pytest.mark.asyncio
    async def test_basic_file_is_ignored(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """A BasicFile falls through both branches → no stamp, no error."""
        root = stash_creator_root(respx_stash_processor)
        bfile = BasicFile(id="804", path=f"{root}/test_user/x_id_42.txt")
        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.txt")
        original_stash_id = media.stash_id
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        index = {PurePath(bfile.path).name: (media, [mock_item])}

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="ERROR")
        try:
            result = await respx_stash_processor._process_file_first(
                bfile, index, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)

        assert result == []
        assert media.stash_id == original_stash_id
        assert "foreign co-resident" not in sink.getvalue()

    @pytest.mark.asyncio
    async def test_image_set_but_pathless_refetched_then_stamped(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Live-contract: file carries a SET-but-PATH-LESS image → re-fetch.

        Replicates SGC 0.12.8 on the live server: ``file.images`` nests an Image
        whose ``visual_files`` is SET but each ``vf.path`` is UNSET. A no-refetch
        dispatcher would read those UNSET paths, the helper would return False,
        and the image would be WRONGLY skipped. The fix invalidates the path-less
        image and re-fetches a path-resolved copy via the find fragment
        (``get_many`` → ``findImage``). Load-bearing discriminator: the FRESH
        (path-resolved) image is stamped + stash_id set — impossible without the
        re-fetch; the path-less in-memory copy is left untouched.
        """
        root = stash_creator_root(respx_stash_processor)
        # The file carries a path-less image (visual_files SET, vf.path UNSET) —
        # the live-server shape that forces the re-fetch.
        pathless_vf = ImageFile.model_construct(id="811")
        assert not is_set(pathless_vf.path)
        pathless_image = Image.model_construct(
            id="950", title="Fresh", visual_files=[pathless_vf]
        )
        stale_file = ImageFile.model_construct(
            id="810", path=f"{root}/u/x_id_42.jpg", images=[pathless_image]
        )

        # Real re-fetch: findImage returns the PATH-RESOLVED copy (a fresh object,
        # not the path-less one), with its visual file under our root.
        image_data = create_image_dict(
            id="950",
            title="Fresh",
            performers=[],
            visual_files=[
                create_image_file_dict("811", f"{root}/test_user/x_id_42.jpg")
            ],
        )
        # One call: the re-fetch findImage (the stamp fires no incidental query).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_data)
                )
            ]
        )

        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.jpg")
        studio = seed_processor_caches(respx_stash_processor, mock_account)
        index = {PurePath(stale_file.path).name: (media, [mock_item])}

        try:
            result = await respx_stash_processor._process_file_first(
                stale_file, index, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "image_set_but_pathless")

        # The FRESH (path-resolved) image was stamped — not the path-less one.
        assert len(result) == 1
        assert result[0].id == "950"
        assert result[0].code == str(media.id)
        # The path-less in-memory copy was NOT stamped (a different object).
        assert not is_set(pathless_image.code)
        assert media.stash_id == 950
        # The re-fetch fired: a findImage query was issued.
        assert any(b"findImage" in c.request.content for c in route.calls)

    @pytest.mark.asyncio
    async def test_image_refetch_returns_empty_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Fail-safe: re-fetch returns [] (image gone) → skip, no stamp, no crash.

        Between sweep and re-fetch the image may be deleted; ``find_by_id`` reads
        ``findImage: null`` so ``get_many`` returns ``[]``. The dispatcher must
        NOT index ``[0]`` blindly — it logs and skips, leaving ``media.stash_id``
        unchanged.
        """
        root = stash_creator_root(respx_stash_processor)
        our = ImageFile(id="820", path=f"{root}/test_user/x_id_42.jpg")
        our.images = [Image(id="960")]  # supplies the loop id

        # Real re-fetch: findImage returns null (image deleted) → get_many → [].
        # One call: the re-fetch findImage returns null (image deleted).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(200, json=create_graphql_response("findImage", None))
            ]
        )
        media = MediaFactory.build(is_downloaded=True, local_filename="x_id_42.jpg")
        original_stash_id = media.stash_id
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        index = {PurePath(our.path).name: (media, [mock_item])}

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="ERROR")
        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)
            dump_graphql_calls(route.calls, "image_refetch_returns_empty")

        # Logged + skipped (fail-safe); no stamp, stash_id unchanged, empty list.
        assert result == []
        assert "960" in sink.getvalue()
        assert media.stash_id == original_stash_id
        assert media.stash_id is None
