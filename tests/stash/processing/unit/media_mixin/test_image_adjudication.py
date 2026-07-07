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
from pathlib import Path, PurePath

import httpx
import pytest
import respx
from loguru import logger as loguru_logger
from stash_graphql_client import present
from stash_graphql_client.types import (
    BasicFile,
    GalleryFile,
    Image,
    ImageFile,
)
from stash_graphql_client.types.unset import is_set

from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash import (
    create_graphql_response,
    create_image_dict,
    create_image_file_dict,
    seed_processor_caches,
    stash_creator_root,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls


_GRAPHQL_URL = "http://localhost:9999/graphql"
_MAPPED_ROOT = "/stash/library/fansly"


class TestImageFilesAllLocal:
    """Tests for MediaProcessingMixin._image_files_all_local (pure path logic).

    One parametrized table over (visual file paths, mapped-override config,
    expected verdict). ``{root}`` in a path is formatted with the creator root
    at run time (the root is fixture-dependent); ``None`` paths mean
    visual_files is left UNSET. The sibling-prefix row appends to the root with
    NO separator: root ``/dl/anna`` must not claim ``/dl/annabelle/...`` —
    without a separator-boundary anchor a bare prefix match would treat the
    sibling's file as local, skipping the foreign-coresident guard. The
    mapped-override rows set ``stash_override_dldir_w_mapped`` so the root is
    ``str(mapped_path)`` regardless of base_path.
    """

    @pytest.mark.parametrize(
        ("image_id", "visual_paths", "mapped_override", "expected"),
        [
            # Every visual file under the creator root → all-local.
            pytest.param(
                "3001",
                ["{root}/test_user/x_id_1.jpg", "{root}/test_user/x_id_2.jpg"],
                False,
                True,
                id="all-local-true",
            ),
            # A single co-resident outside the root → not all-local.
            pytest.param(
                "3002",
                ["{root}/test_user/x_id_1.jpg", "/other/scraper/foreign.jpg"],
                False,
                False,
                id="one-foreign-false",
            ),
            # Sibling creator sharing a name prefix (rootbelle/...) is foreign.
            pytest.param(
                "3006",
                ["{root}/test_user/x_id_1.jpg", "{root}belle/other_user/x_id_2.jpg"],
                False,
                False,
                id="sibling-prefix-is-foreign-false",
            ),
            # Empty visual_files → cannot verify ownership → False (skip).
            pytest.param("3003", [], False, False, id="empty-visual-files-false"),
            # UNSET visual_files → cannot verify ownership → False (skip).
            pytest.param("3004", None, False, False, id="unset-visual-files-false"),
            # override + mapped_path: under the mapped root → local.
            pytest.param(
                "3005",
                [f"{_MAPPED_ROOT}/test_user/x_id_1.jpg"],
                True,
                True,
                id="mapped-override-local-true",
            ),
            # override + mapped_path: a path outside the mapped root → False.
            pytest.param(
                "3007",
                [
                    f"{_MAPPED_ROOT}/test_user/x_id_1.jpg",
                    "/some/other/mount/x_id_2.jpg",
                ],
                True,
                False,
                id="mapped-override-foreign-false",
            ),
        ],
    )
    def test_image_files_all_local(
        self,
        respx_stash_processor: StashProcessing,
        image_id: str,
        visual_paths: list[str] | None,
        mapped_override: bool,
        expected: bool,
    ) -> None:
        """Path-ownership verdict for each visual-file layout."""
        if mapped_override:
            respx_stash_processor.config.stash_mapped_path = Path(_MAPPED_ROOT)
            respx_stash_processor.config.stash_override_dldir_w_mapped = True
            # Sanity: the helper's root is the mapped path, not the tmp base_path.
            assert stash_creator_root(respx_stash_processor) == _MAPPED_ROOT
        root = stash_creator_root(respx_stash_processor)

        if visual_paths is None:
            image = Image(id=image_id)  # visual_files defaults to UNSET
        else:
            image = Image(
                id=image_id,
                visual_files=[
                    ImageFile(id=str(i + 1), path=path.format(root=root))
                    for i, path in enumerate(visual_paths)
                ],
            )
        assert respx_stash_processor._image_files_all_local(image) is expected


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
        index = {PurePath(present(our.path)).name: (media, [mock_item])}

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

        index = {PurePath(present(our.path)).name: (media, [mock_item])}

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

        index = {PurePath(present(gfile.path)).name: (media, [mock_item])}

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

        index = {PurePath(present(bfile.path)).name: (media, [mock_item])}

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
        index = {PurePath(present(stale_file.path)).name: (media, [mock_item])}

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

        index = {PurePath(present(our.path)).name: (media, [mock_item])}

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
