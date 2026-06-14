"""Media processing mixin.

This mixin handles file-first adjudication of swept Stash files against the
creator's downloaded media: resolving ownership of scenes/images, splitting
scenes off shared libraries, and stamping metadata. The incremental fast-path
(``_process_media_fast_path``) adjudicates a single Media via its stored
``stash_id`` without a full sweep.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import TYPE_CHECKING

from stash_graphql_client import is_set
from stash_graphql_client.types import (
    Image,
    ImageFile,
    Scene,
    Studio,
    VideoFile,
)

from metadata import Account, Media
from pathio import get_stash_path

from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


if TYPE_CHECKING:
    from collections.abc import Sequence

    from stash_graphql_client.types import BaseFile, Performer

    from metadata import Message, Post


class MediaProcessingMixin(StashProcessingProtocol):
    """Media processing functionality."""

    async def _owned_scene(self, file: VideoFile) -> Scene | None:
        """Return the scene our file is primary of (files[0]), else None.

        Resolves the reverse field on an uncapable server (UNSET -> populate).
        """
        if not is_set(file.scenes):
            # populate may return a DIFFERENT instance (force/refetch builds a
            # fresh object); rebind so we read the populated scenes, not stale.
            file = await self.store.populate(file, ["scenes"])
        scenes = file.scenes if is_set(file.scenes) and file.scenes else []
        return next(
            (s for s in scenes if is_set(s.files) and s.files and file == s.files[0]),
            None,
        )

    def _image_files_all_local(self, image: Image) -> bool:
        """True iff every visual file of the image is under our creator root.

        A foreign co-resident (e.g. another scraper's copy pHash-merged into the
        same Stash image) means we can't cleanly own this image — caller skips.
        """
        if not self.state.base_path:
            return False  # no root to compute -> can't verify -> skip
        if not is_set(image.visual_files) or not image.visual_files:
            return False  # can't verify ownership -> treat as not-all-local (skip)
        root = get_stash_path(self.state.base_path, self.config).rstrip("/")
        # Anchor on a separator boundary: a bare prefix match lets a sibling
        # creator '/dl/annabelle/...' pass as local under root '/dl/anna'.
        return all(
            is_set(vf.path)
            and vf.path
            and (vf.path == root or vf.path.startswith(root + "/"))
            for vf in image.visual_files
        )

    async def _split_scene_for_file(
        self,
        file: BaseFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> Scene:
        """Split: create a NEW store-tracked Scene that owns ``file``.

        The new scene is built with ``files=[file]`` (temp UUID id → is_new),
        registered via ``store.add`` so a later ``save_all`` emits
        ``SceneCreateInput(file_ids=[file.id])`` — making our file the new
        scene's primary and reassigning it off every scene where it was a
        secondary. We never re-own the foreign shared scene (no
        ``set_primary_file``); we never call the client ``create_scene``.
        Returns the new scene (accumulated for the batched ``save_all`` flush).
        """
        new_scene = Scene(files=[file])
        # Brand-new scene has no server-side relationships; initialize the lists
        # _stamp_metadata mutates so add_performer/add_tag stay in-memory rather
        # than populating a not-yet-persisted (temp-id) scene from the server.
        new_scene.performers = []
        new_scene.tags = []
        self.store.add(new_scene)
        await self._stamp_metadata(new_scene, media, item, account, studio)
        return new_scene

    async def _process_file_first(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Adjudicate one swept Stash file against our downloaded media.

        Resolves the file to our (Media, owning items) via the leaf-name index,
        using the earliest (canonical) owner for the entity's own metadata, then:
        VideoFile + we are the scene's primary -> stamp metadata in place and
        record `media.stash_id`; not-owned -> split/dry-run/warn; ImageFile ->
        detect-and-log / stamp.

        Returns the Stash entities (Scene/Image) our file was adjudicated into,
        so a later phase can compose galleries from the (possibly pre-flush,
        temp-id) entities themselves. Returns an EMPTY list for any skip,
        unindexed, indeterminate, dry-run, or disabled path.
        """
        if not is_set(file.path):
            return []  # a swept file with no fetched path cannot be indexed — skip
        leaf_name = PurePath(file.path).name
        media_item = index.get(leaf_name)
        if media_item is None:
            return []  # a Stash file with no matching FDNG download — skip
        media, owners = media_item
        item = owners[0]  # earliest owner is canonical for the entity's metadata

        if isinstance(file, VideoFile):
            if media.mimetype and media.mimetype.startswith("image/"):
                # An animated image (gif/webp/apng) is an Image backed by a
                # VideoFile, which exposes no `.images` reverse — we cannot reach
                # its Image to adjudicate. Skip (detect-and-log) rather than
                # mis-route to scene-split, which would create a spurious Scene.
                logger.warning(
                    f"{leaf_name} (file {file.id}) is an animated "
                    f"image (media {media.id}, {media.mimetype}) backed by a "
                    f"VideoFile with no resolvable Image; skipping (no split)."
                )
            else:
                owned = await self._owned_scene(file)
                if owned is not None:
                    await self._stamp_metadata(owned, media, item, account, studio)
                    media.stash_id = int(owned.id)
                    return [owned]
                # Not-owned (secondary on a shared scene): split / dry-run / warn.
                return await self._adjudicate_not_owned(
                    file, media, item, account, studio
                )
        elif isinstance(file, ImageFile):
            return await self._adjudicate_image(file, media, item, account, studio)
        # GalleryFile / BasicFile, or the animated-image skip above -> ignore.
        return []

    async def _adjudicate_image(
        self,
        file: ImageFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Adjudicate a swept ImageFile: detect-and-log shared images, else stamp.

        Images cannot be cleanly split (Stash offers only set-primary/delete), so
        the rule is detect-and-log: if an Image using our file has ANY foreign
        (not-under-our-root) co-resident visual file, we ``logger.error`` and skip
        — never re-owning a shared image (mirroring the scene side never re-owning
        a shared scene). If every visual file is ours, we stamp and record
        ``media.stash_id``.

        Path-resolution caveat: ``populate(image, ["visual_files"])`` does not
        reliably resolve ``visual_files[].path`` (it can silently leave them
        UNSET), so ownership is checked by re-fetching each Image fresh via the
        find fragment — image IDs off ``file.images`` + ``get_many``, invalidating
        first so a cached path-less copy is not read.
        """
        if not is_set(file.path):
            raise ValueError(
                f"ImageFile {file.id} has no resolved path; cannot adjudicate."
            )
        leaf_name = PurePath(file.path).name
        if not is_set(file.images):
            file = await self.store.populate(file, ["images"])  # REBIND
        stamped: list[Scene | Image] = []
        for stale in file.images if is_set(file.images) and file.images else []:
            # Evict any cached copy (whose visual_files paths may be UNSET) so the
            # find-fragment re-fetch returns visual_files WITH paths resolved.
            self.store.invalidate(Image, stale.id)
            fresh = await self.store.get_many(Image, [stale.id])
            image = fresh[0] if fresh else None
            if image is None or not self._image_files_all_local(image):
                # Foreign co-resident OR ownership unresolvable -> fail safe: skip.
                files = (
                    []
                    if image is None
                    else (
                        image.visual_files
                        if is_set(image.visual_files) and image.visual_files
                        else []
                    )
                )
                logger.error(
                    f"Image {stale.id} for {leaf_name} has a "
                    f"foreign co-resident (or paths unresolved); not stamping "
                    f"shared image. Files: {[getattr(vf, 'path', None) for vf in files]}"
                )
                continue
            await self._stamp_metadata(image, media, item, account, studio)
            stamped.append(image)
        if stamped:
            # Deterministic when a file backs multiple owned images (get_many
            # order is not stable): record the smallest id.
            media.stash_id = min(int(img.id) for img in stamped)
        return stamped

    async def _adjudicate_not_owned(
        self,
        file: VideoFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Handle a swept file that is a SECONDARY on a scene another source owns.

        3-way dispatch on ``config.stash_enable_scene_split``: ``"dry-run"`` logs
        the would-be split; ``False`` (or any non-True) warns and skips; ``True``
        SPLITs — but only after a fail-safe authoritative re-check, since the
        non-forced ``_owned_scene`` read can yield a stale "not owned" and a
        spurious split would duplicate a scene on the shared library.

        Returns the newly-split scene in a one-element list, or an EMPTY list for
        dry-run / disabled / indeterminate (no scene was adjudicated into).
        """
        if not is_set(file.path):
            raise ValueError(
                f"VideoFile {file.id} has no resolved path; cannot adjudicate split."
            )
        leaf_name = PurePath(file.path).name
        mode = self.config.stash_enable_scene_split
        shared_ids = [
            s.id for s in (file.scenes if is_set(file.scenes) and file.scenes else [])
        ]
        if mode == "dry-run":
            logger.info(
                f"[scene-split dry-run] would split {leaf_name} "
                f"(file {file.id}) off shared scene(s) {shared_ids} into a new "
                f"owned scene with code={media.id}, studio + performers + tags."
            )
            return []
        if mode is not True:  # False (or any non-True): skip with a warning
            logger.warning(
                f"{leaf_name} (file {file.id}) is a secondary on "
                f"shared scene(s) {shared_ids}; scene-split disabled — skipping."
            )
            return []
        # mode is True -> SPLIT, but FAIL-SAFE re-confirm first. The non-forced
        # _owned_scene read can yield a stale/empty "not owned"; re-resolve the
        # reverse field authoritatively before the destructive create. force_refetch
        # invalidates the cache and may return a FRESH instance — rebind to it (the
        # local `file` stays stale), and use it for the re-check and the split.
        file = await self.store.populate(file, ["scenes"], force_refetch=True)
        if not is_set(file.scenes):
            logger.warning(
                f"Could not resolve scenes for {leaf_name} "
                f"(file {file.id}); ownership indeterminate — skipping split."
            )
            return []
        scenes = file.scenes if is_set(file.scenes) and file.scenes else []
        if any(not is_set(s.files) for s in scenes):
            # A candidate scene's files couldn't be resolved -> we CANNOT confirm
            # we aren't its primary. A fail-safe must err toward NOT splitting.
            logger.warning(
                f"Scene files unresolved for {leaf_name} "
                f"(file {file.id}); ownership indeterminate — skipping split."
            )
            return []
        owns_a_scene = any(
            is_set(s.files) and s.files and file == s.files[0] for s in scenes
        )
        if owns_a_scene:
            # We ARE primary after the fresh read — do NOT split (would dup).
            logger.debug(
                f"{leaf_name} resolved as primary on re-check; "
                f"skipping split (owned path handles updates)."
            )
            return []
        new_scene = await self._split_scene_for_file(file, media, item, account, studio)
        return [new_scene]

    async def _process_media_fast_path(
        self,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Incremental entry point: adjudicate a Media via its stored stash_id.

        Fetches the stored Scene/Image by id (skipping the sweep), re-verifies our
        file is still its primary (a pHash re-merge may have demoted us), then
        stamps (still owned) or re-adjudicates (demoted scene -> split per
        enable_scene_split; image -> detect-and-log). Returns the adjudicated
        entities, or [] when nothing applies (stale id / file no longer attached).
        """
        if media.stash_id is None or not media.local_filename:
            return []
        is_image = media.mimetype is not None and media.mimetype.startswith("image/")
        entity_type = Image if is_image else Scene
        # store.get is cache-first; a cached entity can hold UNSET file paths,
        # which break the basename match. Evict so the get re-fetches with paths.
        self.store.invalidate(entity_type, str(media.stash_id))
        entity = await self.store.get(entity_type, str(media.stash_id))
        if not isinstance(entity, (Scene, Image)):
            return []  # stale/deleted stash_id — caller may fall back to sweep
        leaf = PurePath(media.local_filename).name
        our_file = self._locate_file_by_leaf(entity, leaf)
        if our_file is None:
            return []  # our file is no longer attached to this entity
        if isinstance(entity, Image):
            return await self._fast_path_image(entity, media, item, account, studio)
        return await self._fast_path_scene(
            entity, our_file, media, item, account, studio
        )

    async def _fast_path_scene(
        self,
        entity: Scene,
        our_file: BaseFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Fast-path a Scene reached via the stored stash_id: stamp if our file
        is still its primary, else re-adjudicate the demoted file via the
        not-owned (split / dry-run / warn) path. Only a VideoFile can be split.
        """
        # still primary -> stamp
        if is_set(entity.files) and entity.files and our_file == entity.files[0]:
            await self._stamp_metadata(entity, media, item, account, studio)
            media.stash_id = int(entity.id)
            return [entity]
        # Demoted (no longer files[0]) -> re-adjudicate via the not-owned path.
        if not isinstance(our_file, VideoFile):
            return []
        return await self._adjudicate_not_owned(our_file, media, item, account, studio)

    @staticmethod
    def _locate_file_by_leaf(entity: Scene | Image, leaf: str) -> BaseFile | None:
        """The entity's video/visual file whose path basename matches ``leaf``."""
        # Scene.files is list[VideoFile]; Image.visual_files is the wider
        # list[VideoFile | ImageFile] — read through the covariant BaseFile view.
        files: Sequence[BaseFile]
        if isinstance(entity, Image):
            files = (
                entity.visual_files
                if is_set(entity.visual_files) and entity.visual_files
                else []
            )
        else:
            files = entity.files if is_set(entity.files) and entity.files else []
        return next(
            (
                f
                for f in files
                if is_set(f.path) and f.path and PurePath(f.path).name == leaf
            ),
            None,
        )

    async def _fast_path_image(
        self,
        entity: Image,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]:
        """Fast-path image branch: stamp an all-local image, else detect-and-log.

        Mirrors the sweep's detect-and-log rule: a foreign co-resident visual
        file means we never re-own a shared image.
        """
        if not self._image_files_all_local(entity):
            logger.error(
                f"Image {entity.id} (fast-path, media {media.id}) has a foreign "
                f"co-resident; not stamping shared image."
            )
            return []
        await self._stamp_metadata(entity, media, item, account, studio)
        media.stash_id = int(entity.id)
        return [entity]

    async def _stamp_metadata(
        self,
        stash_obj: Scene | Image,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> None:
        """Stamp metadata onto a Stash Scene or Image from a resolved Media.

        Mutates ``stash_obj`` in place and leaves it dirty; a later phase flushes
        via ``store.save_all()``. ``code`` is stamped as ``str(media.id)`` and
        ``media.is_preview`` drives the "Trailer" tag.

        Args:
            stash_obj: Scene or Image to stamp
            media: Resolved Media providing the code id and preview flag
            item: Post or Message containing metadata
            account: Account that created the content
            studio: Pre-resolved studio (avoids repeated lookups per media item)
        """
        if self._should_skip_stamp(stash_obj, item):
            return
        self._apply_core_fields(stash_obj, media, item, account)
        await self._stamp_performers(stash_obj, item, account)
        await self._stamp_studio(stash_obj, account, studio)
        await self._stamp_tags(stash_obj, item, media)

    def _should_skip_stamp(
        self, stash_obj: Scene | Image, item: Post | Message
    ) -> bool:
        """Whether to leave the object untouched.

        Skip an already-organized object, or one whose stored date is earlier
        than the item (preserve the earliest). A legacy "Media from..." title
        forces a re-stamp regardless.
        """
        current_title = getattr(stash_obj, "title", None) or ""
        if "Media from" in current_title:
            return False
        if getattr(stash_obj, "organized", False):
            return True
        current_date_str = getattr(stash_obj, "date", None)
        if not current_date_str:
            return False
        try:
            current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()  # noqa: DTZ007
        except ValueError:
            logger.warning(f"Invalid date format in stash object: {current_date_str}")
            return False
        if item.createdAt is None:
            raise ValueError(f"item {item.id} has no createdAt; cannot compare dates.")
        return item.createdAt.date() > current_date

    def _apply_core_fields(
        self,
        stash_obj: Scene | Image,
        media: Media,
        item: Post | Message,
        account: Account,
    ) -> None:
        """Stamp title/details/date/code in place; add the post URL (posts only)."""
        if item.createdAt is None:
            raise ValueError(f"item {item.id} has no createdAt; cannot stamp metadata.")
        created_at = item.createdAt
        stash_obj.title = self._generate_title_from_content(
            content=item.content,
            username=account.username,
            created_at=created_at,
        )
        stash_obj.details = item.content
        stash_obj.date = created_at.date().strftime("%Y-%m-%d")
        stash_obj.code = str(media.id)
        # Message URLs do not resolve for other users; only posts carry a URL.
        if item.__class__.__name__ == "Post":
            post_url = f"https://fansly.com/post/{item.id}"
            if not is_set(stash_obj.urls) or not stash_obj.urls:
                stash_obj.urls = []
            if post_url not in stash_obj.urls:
                stash_obj.urls.append(post_url)

    async def _stamp_performers(
        self, stash_obj: Scene | Image, item: Post | Message, account: Account
    ) -> None:
        """Add the primary creator performer (cached) plus any mentioned ones."""
        main_performer: Performer | None
        if self._account and account.id == self._account.id and self._performer:
            main_performer = self._performer
        else:
            main_performer = await self._find_existing_performer(account)
        if main_performer:
            await stash_obj.add_performer(main_performer)
        for mention in getattr(item, "mentions", None) or []:
            mention_performer = await self._get_or_create_performer(mention)
            if mention_performer:
                # PostMention has no stash_id; _update_account_stash_id no-ops it.
                await self._update_account_stash_id(mention, mention_performer)
                await stash_obj.add_performer(mention_performer)

    async def _stamp_studio(
        self, stash_obj: Scene | Image, account: Account, studio: Studio | None
    ) -> None:
        """Set the studio: pre-resolved, cached, or looked up once."""
        if studio is None:
            studio = self._studio
        if studio is None:
            studio = await self._find_existing_studio(account)
        if not studio:
            return
        if hasattr(stash_obj, "set_studio"):  # Scene has the helper, Image does not
            stash_obj.set_studio(studio)
        else:
            stash_obj.studio = studio

    async def _stamp_tags(
        self, stash_obj: Scene | Image, item: Post | Message, media: Media
    ) -> None:
        """Add hashtag tags and the preview ('Trailer') tag when applicable."""
        hashtags = getattr(item, "hashtags", None)
        if hashtags:
            for tag in await self._process_hashtags_to_tags(hashtags):
                await stash_obj.add_tag(tag)
        if media.is_preview:
            await self._add_preview_tag(stash_obj)
