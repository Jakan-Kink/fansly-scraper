"""Media processing mixin.

This mixin handles file-first adjudication of swept Stash files against the
creator's downloaded media: resolving ownership of scenes/images, splitting
scenes off shared libraries, and stamping metadata. The incremental fast-path
(``_process_media_fast_path``) adjudicates a single Media via its stored
``stash_id`` without a full sweep.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from stash_graphql_client import is_set
from stash_graphql_client.types import (
    Image,
    ImageFile,
    Scene,
    Studio,
    VideoFile,
)

from metadata import Account, Media, Post
from metadata.models import get_store
from pathio import get_stash_path

from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


if TYPE_CHECKING:
    from collections.abc import Sequence

    from stash_graphql_client.types import BaseFile, Performer

    from metadata import Message, Post


_FANSLY_POST_URL = re.compile(r"https://fansly\.com/post/\d+")
_FILENAME_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})_at_")


def _filename_date(name: str | None) -> str | None:
    """The date a downloaded file's name starts with, if it has one."""
    found = _FILENAME_DATE.match(PurePath(name).name) if name else None
    return found.group(1) if found else None


# Scene/Image fields that stamping reads or writes.
_STAMP_FIELDS = (
    "code",
    "date",
    "details",
    "organized",
    "performers",
    "studio",
    "tags",
    "title",
    "urls",
)


class MediaProcessingMixin(StashProcessingProtocol):
    """Media processing functionality."""

    def _is_fansly_file(self, file: BaseFile) -> bool:
        """True iff the file's path is known and inside the download directory.

        Every creator folder counts: the same media is saved under each creator
        that posts or quotes it, and Stash merges those copies into one entry.
        """
        if not is_set(file.path) or not file.path:
            return False
        roots = [
            get_stash_path(Path(base), self.config).rstrip("/")
            for base in (self.config.download_directory, self.state.base_path)
            if base
        ]
        # Anchor on a separator boundary: a bare prefix match lets '/dl/fansly2'
        # pass as inside '/dl/fansly'.
        return any(
            file.path == root or file.path.startswith(root + "/") for root in roots
        )

    async def _owned_scene(self, file: VideoFile) -> Scene | None:
        """Return the scene whose primary file (files[0]) is ours, else None.

        Ours means this file or another copy inside the download directory.
        Resolves the reverse field on an uncapable server (UNSET -> populate).
        """
        if not is_set(file.scenes):
            # populate may return a DIFFERENT instance (force/refetch builds a
            # fresh object); rebind so we read the populated scenes, not stale.
            file = await self.store.populate(file, ["scenes"])
        for scene in file.scenes if is_set(file.scenes) and file.scenes else []:
            if not is_set(scene.files) or not scene.files:
                continue
            primary = scene.files[0]
            if file == primary or self._is_fansly_file(primary):
                return scene
            if is_set(primary.path):
                continue  # a foreign primary
            # The primary's path was not read with the file; read the scene.
            full = await self._complete_entity(scene)
            if (
                full is not None
                and is_set(full.files)
                and full.files
                and self._is_fansly_file(full.files[0])
            ):
                return full
        return None

    async def _complete_entity[EntityT: Scene | Image](
        self, entity: EntityT
    ) -> EntityT | None:
        """Return ``entity`` with everything stamping and ownership checks read.

        The store's copy is returned untouched when it already received those
        fields and every file's path; otherwise it is re-read once by id.

        Args:
            entity: Scene or Image from the store

        Returns:
            The complete entity, or None when Stash no longer has it.
        """
        files_field = "visual_files" if isinstance(entity, Image) else "files"
        if not self.store.missing_fields_nested(
            entity, f"{files_field}__path", *_STAMP_FIELDS
        ):
            return entity
        self.store.invalidate(type(entity), entity.id)
        return await self.store.get(type(entity), entity.id)

    def _image_files_all_local(self, image: Image) -> bool:
        """True iff every visual file of the image is inside the download directory.

        A foreign co-resident (e.g. another scraper's copy pHash-merged into the
        same Stash image) means we can't cleanly own this image — caller skips.
        """
        if not is_set(image.visual_files) or not image.visual_files:
            return False  # can't verify ownership -> treat as not-all-local (skip)
        return all(self._is_fansly_file(vf) for vf in image.visual_files)

    async def _split_scene_for_file(
        self,
        file: BaseFile,
        media: Media,
        item: Post | Message | None,
        account: Account,
        studio: Studio | None = None,
        siblings: Sequence[BaseFile] = (),
    ) -> Scene:
        """Split: create a NEW store-tracked Scene that owns ``file``.

        The new scene is built with ``files=[file, *siblings]`` (temp UUID id →
        is_new), registered via ``store.add`` so a later ``save_all`` emits
        ``SceneCreateInput(file_ids=[...])`` — making our file the new scene's
        primary and reassigning each file off every scene where it was a
        secondary. ``siblings`` are the other download-directory copies on the
        shared scene, so they leave it together. We never re-own the foreign
        shared scene (no ``set_primary_file``); we never call the client
        ``create_scene``. Returns the new scene (accumulated for the batched
        ``save_all`` flush).
        """
        new_scene = Scene(files=[file, *siblings])
        # Brand-new scene has no server-side relationships; initialize the lists
        # stamping and gallery linking mutate so add_performer/add_tag/add_scene
        # stay in-memory rather than populating a not-yet-persisted (temp-id)
        # scene from the server.
        new_scene.performers = []
        new_scene.tags = []
        new_scene.galleries = []
        self.store.add(new_scene)
        if item is None:
            await self._stamp_unposted(new_scene, media, account, studio)
        else:
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
        leaf_name = PurePath(file.path).name if is_set(file.path) else None
        media_item = index.get(leaf_name) if leaf_name else None
        if media_item is None:
            # No fetched path to index by, or no matching FDNG download — skip.
            return []
        media, owners = media_item
        if not owners:
            return await self._link_unposted_file(file, media, account, studio)
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
                    await self._split_foreign_files(owned)
                    return [owned]
                # Not-owned (secondary on a shared scene): split / dry-run / warn.
                return await self._adjudicate_not_owned(
                    file, media, item, account, studio
                )
        elif isinstance(file, ImageFile):
            return await self._adjudicate_image(file, media, item, account, studio)
        # GalleryFile / BasicFile, or the animated-image skip above -> ignore.
        return []

    async def _link_unposted_file(
        self,
        file: BaseFile,
        media: Media,
        account: Account,
        studio: Studio | None,
    ) -> list[Scene | Image]:
        """Link a file whose post is not in the database to its creator.

        Stamps the scene our file is primary of, or the images whose files are
        all ours. A video that is a secondary on another source's scene is
        split off as it would be with a post.

        Args:
            file: The swept Stash file
            media: The Media recorded for it
            account: The Account being processed
            studio: Its pre-resolved studio

        Returns:
            The Scene or Images stamped; empty when another source owns them.
        """
        targets: list[Scene | Image] = []
        is_image = bool(media.mimetype and media.mimetype.startswith("image/"))
        if isinstance(file, VideoFile) and not is_image:
            owned = await self._owned_scene(file)
            if owned is None:
                return await self._adjudicate_not_owned(
                    file, media, None, account, studio
                )
            targets.append(owned)
        elif isinstance(file, ImageFile):
            image_file = (
                file
                if is_set(file.images)
                else await self.store.populate(file, ["images"])
            )
            images = image_file.images if is_set(image_file.images) else None
            for found in images or []:
                image = await self._complete_entity(found)
                if image is not None and self._image_files_all_local(image):
                    targets.append(image)
        stamped: list[Scene | Image] = []
        for target in targets:
            if getattr(target, "organized", False):
                continue
            await self._stamp_unposted(target, media, account, studio)
            stamped.append(target)
        if stamped:
            media.stash_id = min(int(entity.id) for entity in stamped)
        return stamped

    async def _stamp_unposted(
        self,
        stash_obj: Scene | Image,
        media: Media,
        account: Account,
        studio: Studio | None,
    ) -> None:
        """Stamp what a file and its creator provide when no post is known.

        Sets the media id as code and a date (the media's, else the one in the
        file name) where those are empty, and adds the performer, the studio
        and the preview tag.

        Args:
            stash_obj: Scene or Image to stamp
            media: The Media recorded for the file
            account: The Account being processed
            studio: Its pre-resolved studio
        """
        if not is_set(stash_obj.code) or not stash_obj.code:
            stash_obj.code = str(media.id)
        day = media.createdAt.date().isoformat() if media.createdAt else None
        day = day or _filename_date(media.local_filename)
        if day and (not is_set(stash_obj.date) or not stash_obj.date):
            stash_obj.date = day
        await self._stamp_performers(stash_obj, None, account)
        await self._stamp_studio(stash_obj, account, studio)
        await self._stamp_tags(stash_obj, None, media)

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

        Ownership needs every visual file's path. The Image nested on the file
        is used as is when the store holds those; a copy without them (SGC can
        nest path-less visual files) is re-read once by id.
        """
        if not is_set(file.path):
            raise ValueError(
                f"ImageFile {file.id} has no resolved path; cannot adjudicate."
            )
        leaf_name = PurePath(file.path).name
        if not is_set(file.images):
            file = await self.store.populate(file, ["images"])  # REBIND
        stamped: list[Scene | Image] = []
        for found in file.images if is_set(file.images) and file.images else []:
            image = await self._complete_entity(found)
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
                    f"Image {found.id} for {leaf_name} has a "
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
        item: Post | Message | None,
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
        # Every file's path is needed to tell our copies from foreign ones.
        shared: list[list[VideoFile]] = []
        for candidate in file.scenes if is_set(file.scenes) and file.scenes else []:
            scene: Scene | None = candidate
            if not is_set(candidate.files) or any(
                not is_set(f.path) for f in candidate.files
            ):
                scene = await self._complete_entity(candidate)
            if scene is None or not is_set(scene.files):
                # A candidate scene's files couldn't be resolved -> we CANNOT
                # confirm we aren't its primary. Err toward NOT splitting.
                logger.warning(
                    f"Scene files unresolved for {leaf_name} "
                    f"(file {file.id}); ownership indeterminate — skipping split."
                )
                return []
            shared.append(scene.files)
        owns_a_scene = any(
            files and (file == files[0] or self._is_fansly_file(files[0]))
            for files in shared
        )
        if owns_a_scene:
            # A copy of ours IS primary after the fresh read — do NOT split.
            logger.debug(
                f"{leaf_name} resolved as primary on re-check; "
                f"skipping split (owned path handles updates)."
            )
            return []
        siblings = {
            f.id: f
            for files in shared
            for f in files
            if f != file and self._is_fansly_file(f)
        }
        new_scene = await self._split_scene_for_file(
            file, media, item, account, studio, list(siblings.values())
        )
        return [new_scene]

    async def _split_foreign_files(self, scene: Scene) -> None:
        """Move files from outside the download directory off a scene of ours.

        Stash merges the same video from two sources into one scene. The other
        source's files get a new, unstamped scene for its own tool to fill in,
        so each scene holds one source. Follows ``stash_enable_scene_split``:
        off does nothing, ``"dry-run"`` logs. A file whose path is not known
        stays where it is, and an organized scene is left as it is.

        Args:
            scene: A scene whose primary file is ours
        """
        mode = self.config.stash_enable_scene_split
        if getattr(scene, "organized", False):
            return
        files = scene.files if is_set(scene.files) and scene.files else []
        foreign = [
            f
            for f in files[1:]
            if is_set(f.path) and f.path and not self._is_fansly_file(f)
        ]
        if not foreign or (mode is not True and mode != "dry-run"):
            return
        paths = [f.path for f in foreign]
        if mode == "dry-run":
            logger.info(
                f"[scene-split dry-run] would move {paths} off scene {scene.id} "
                f"into a new scene of their own."
            )
            return
        new_scene = Scene(files=foreign)
        try:
            await self.store.save(new_scene)
        except Exception as exc:
            logger.exception(
                f"Failed to move {paths} off scene {scene.id}", exc_info=exc
            )
            self.store.invalidate(new_scene)
            return
        logger.info(f"Moved {paths} off scene {scene.id} into scene {new_scene.id}.")

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
        entity_id = str(media.stash_id)
        # A copy the store already holds (e.g. returned with the swept files)
        # is used when complete; anything else is read once by id.
        cached = self.store.get_cached(entity_type, entity_id)
        entity = (
            await self._complete_entity(cached)
            if isinstance(cached, (Scene, Image))
            else await self.store.get(entity_type, entity_id)
        )
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
        # a copy of ours is still primary -> stamp
        if (
            is_set(entity.files)
            and entity.files
            and (our_file == entity.files[0] or self._is_fansly_file(entity.files[0]))
        ):
            await self._stamp_metadata(entity, media, item, account, studio)
            media.stash_id = int(entity.id)
            await self._split_foreign_files(entity)
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
        ``media.is_preview`` drives the "Trailer" tag. A quoted post's media is
        credited to that post's author, never to the creator who quoted it.

        Args:
            stash_obj: Scene or Image to stamp
            media: Resolved Media providing the code id and preview flag
            item: Post or Message containing metadata
            account: Account being processed
            studio: Its pre-resolved studio (avoids repeated lookups per media item)
        """
        if isinstance(item, Post) and item.accountId != account.id:
            self._drop_quoter_credit(stash_obj, item, account)
        if self._should_skip_stamp(stash_obj, item, media):
            return
        creator = await self._item_creator(item, account, studio)
        if creator is None:
            return
        account, studio = creator
        self._apply_core_fields(stash_obj, media, item, account)
        await self._stamp_performers(stash_obj, item, account)
        await self._stamp_studio(stash_obj, account, studio)
        await self._stamp_tags(stash_obj, item, media)

    async def _item_creator(
        self, item: Post | Message, account: Account, studio: Studio | None
    ) -> tuple[Account, Studio | None] | None:
        """The account and studio an item's content is credited to.

        Args:
            item: Post or Message the content was posted in
            account: Account being processed
            studio: Its pre-resolved studio

        Returns:
            ``(account, studio)`` unchanged for the creator's own items; the
            author's account and studio for a post by someone else (reached
            through a quote); None when that author is not in the metadata
            database.
        """
        if not isinstance(item, Post) or item.accountId == account.id:
            return account, studio
        author = await get_store().get(Account, item.accountId)
        if author is None:
            logger.warning(
                f"Post {item.id} belongs to account {item.accountId}, which is "
                f"not in the metadata database; leaving its media unstamped."
            )
            return None
        return author, await self._find_existing_studio(author)

    def _drop_quoter_credit(
        self, stash_obj: Scene | Image, item: Post, quoter: Account
    ) -> None:
        """Take the quoting creator's credit off a quoted post's scene or image.

        Quoted media used to be stamped from the quoter. Removes the quoter's
        performer, unless the original post mentions them, and every Fansly
        post link other than the original post's. Organized objects are left
        alone.

        Args:
            stash_obj: Scene or Image the quoted media resolved to
            item: The original post
            quoter: Account being processed, whose post quotes ``item``
        """
        if getattr(stash_obj, "organized", False):
            return
        performer = self._performer
        mentioned = any(m.accountId == quoter.id for m in item.mentions or [])
        if (
            performer is not None
            and not mentioned
            and isinstance(stash_obj.performers, list)
            and any(p.id == performer.id for p in stash_obj.performers)
        ):
            stash_obj.performers = [
                p for p in stash_obj.performers if p.id != performer.id
            ]
        own_url = f"https://fansly.com/post/{item.id}"
        if isinstance(stash_obj.urls, list):
            kept = [
                url
                for url in stash_obj.urls
                if url == own_url or not _FANSLY_POST_URL.fullmatch(url)
            ]
            if len(kept) != len(stash_obj.urls):
                stash_obj.urls = kept

    def _should_skip_stamp(
        self, stash_obj: Scene | Image, item: Post | Message, media: Media
    ) -> bool:
        """Whether to leave the object untouched.

        Skip an organized object, one this media already stamped from this item
        (its ``code`` is the media id and, for a post, it carries the post's
        link), or one whose stored date is earlier than the item (preserve the
        earliest). A legacy "Media from..." title forces a re-stamp regardless.
        """
        current_title = getattr(stash_obj, "title", None) or ""
        if "Media from" in current_title:
            return False
        if getattr(stash_obj, "organized", False):
            return True
        if getattr(stash_obj, "code", None) == str(media.id):
            # Stamped from another item (a quoter's post, or a message when
            # the media is also in a post): stamp again from this one.
            urls = stash_obj.urls
            return not (
                isinstance(item, Post)
                and isinstance(urls, list)
                and f"https://fansly.com/post/{item.id}" not in urls
            )
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
        self, stash_obj: Scene | Image, item: Post | Message | None, account: Account
    ) -> None:
        """Add the creator's performer(s) (cached) plus any mentioned ones.

        An account or mention matched to several performers gets all of them.
        """
        main_performer: Performer | None
        if self._account and account.id == self._account.id and self._performer:
            main_performer = self._performer
        else:
            main_performer = await self._get_or_create_performer(account)
            if main_performer:
                await self._update_account_stash_id(account, main_performer)
        performers = [main_performer] if main_performer else []
        performers += self._all_matched_performers(account)
        for mention in getattr(item, "mentions", None) or []:
            mention_performer = await self._get_or_create_performer(mention)
            if mention_performer:
                # PostMention has no stash_id; _update_account_stash_id no-ops it.
                await self._update_account_stash_id(mention, mention_performer)
                performers.append(mention_performer)
                performers += self._all_matched_performers(mention)
        for performer in performers:
            await self._link_performer(stash_obj, performer)

    async def _stamp_studio(
        self, stash_obj: Scene | Image, account: Account, studio: Studio | None
    ) -> None:
        """Set the studio: pre-resolved, cached, or looked up once."""
        if studio is None and self._account and account.id == self._account.id:
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
        self, stash_obj: Scene | Image, item: Post | Message | None, media: Media
    ) -> None:
        """Add hashtag tags and the preview ('Trailer') tag when applicable."""
        hashtags = getattr(item, "hashtags", None)
        if hashtags:
            for tag in await self._process_hashtags_to_tags(hashtags):
                await self._link_tag(stash_obj, tag)
        if media.is_preview:
            await self._add_preview_tag(stash_obj)
