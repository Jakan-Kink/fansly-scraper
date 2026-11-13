"""File-first adjudication flow for StashProcessing.

Owns both lookup entry points feeding the shared adjudication:

- ``_run_file_first`` — the full-creator sweep (also fills ``media.stash_id``).
- ``_run_file_first_incremental`` — the monitoring daemon's sweep-free pass:
  scan just-downloaded files, locate each by basename, adjudicate, flush.

Both reuse ``_prepare_file_first`` (index + accumulators), ``_fast_path_known_media``
(re-verify already-stamped media by id), ``_safe_adjudicate`` (per-file isolation),
and ``_compose_and_flush`` (one gallery per item + a single batched ``save_all``).
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path, PurePath

from stash_graphql_client.types import (
    BaseFile,
    BasicFile,
    Gallery,
    GalleryChapter,
    GalleryFile,
    Image,
    ImageFile,
    Performer,
    Scene,
    Studio,
    UnsetType,
    VideoFile,
)

from helpers.rich_progress import get_progress_manager
from metadata import Account, Media, Message, Post
from metadata.models import get_store
from pathio import get_stash_path
from textio import print_error, print_info

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


class FileFirstProcessingMixin(StashProcessingProtocol):
    """File-first sweep + incremental adjudication, gallery composition, flush."""

    # Re-declare base-class attrs assigned in this mixin's method bodies —
    # without these, mypy infers definitions that conflict with
    # StashProcessingBase's declarations at class-composition time.
    _account: Account | None
    _performer: Performer | None
    _stash_parent_task: str | None

    async def continue_stash_processing(
        self,
        account: Account | None,
        performer: Performer | None,
    ) -> None:
        """Continue processing in background.

        Args:
            account: Account to process
            performer: Performer created from account
        """
        progress_mgr = get_progress_manager()

        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            # Validate performer type (library returns Pydantic objects directly)
            if not isinstance(performer, Performer):
                raise TypeError("performer must be a Stash Performer object")

            self._account = account
            self._performer = performer

            # Convert performer.id (str) for comparison with account.stash_id (int)
            if account.stash_id != int(performer.id):
                await self._update_account_stash_id(
                    account=account,
                    performer=performer,
                )

            # 2 phases: studio, file-first adjudication (sweep + split + galleries)
            performer_label = performer.name or "creator"
            with progress_mgr.session():
                self._stash_parent_task = progress_mgr.add_task(
                    name="stash_creator",
                    description=f"Stash: {performer_label}",
                    total=2,
                    show_elapsed=True,
                )

                # Process creator studio
                print_info("Processing creator Studio...")
                studio = await self.process_creator_studio(account=account)
                self._studio = studio
                progress_mgr.update_task(self._stash_parent_task, advance=1)

                # Sweep the creator's Stash files, adjudicate each, compose
                # galleries, and flush in one batched save_all.
                print_info("Processing creator content (file-first)...")
                await self._run_file_first(account, performer, studio)
                progress_mgr.update_task(self._stash_parent_task, advance=1)

        except Exception as e:
            print_error(f"Error in Stash processing: {e}")
            logger.exception("Error in Stash processing", exc_info=e)
            debug_print(
                {
                    "method": "StashProcessing - continue_stash_processing",
                    "status": "processing_failed",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
            raise
        finally:
            self._finalize_creator(performer)

    async def process_creator_incremental(self) -> None:
        """Daemon entry: awaited, sweep-free incremental Stash pass for one creator.

        Connects, resolves the studio + performer, then runs the incremental
        file-first flow (scan just-downloaded files -> find_one by basename ->
        adjudicate -> batched flush). Awaited inline so the daemon worker only
        marks the creator processed after Stash finishes.
        """
        if not await self._connect_stash():
            return
        account, performer = await self.process_creator()
        performer_obj = performer if isinstance(performer, Performer) else None
        try:
            if not account or not performer_obj:
                return
            self._account = account
            self._performer = performer_obj
            if account.stash_id != int(performer_obj.id):
                await self._update_account_stash_id(
                    account=account, performer=performer_obj
                )
            print_info("Processing creator Studio...")
            studio = await self.process_creator_studio(account=account)
            self._studio = studio
            print_info("Processing creator content (incremental)...")
            await self._run_file_first_incremental(account, performer_obj, studio)
        except Exception as e:
            print_error(f"Error in incremental Stash processing: {e}")
            logger.exception("Error in incremental Stash processing", exc_info=e)
            raise
        finally:
            self._finalize_creator(performer_obj)

    def _finalize_creator(self, performer: Performer | None) -> None:
        """Reset per-creator state, invalidate cached file types, log cache stats."""
        self._stash_parent_task = None
        self._account = None
        self._performer = None
        self._studio = None
        self._galleries_preloaded = False
        # invalidate_type is exact-match on __type_name__ (no subtype cascade),
        # so each concrete BaseFile subtype the sweep may have cached is listed.
        for entity_type in (
            Gallery,
            GalleryChapter,
            Scene,
            Image,
            VideoFile,
            ImageFile,
            GalleryFile,
            BasicFile,
        ):
            self.store.invalidate_type(entity_type)

        performer_name = (
            performer.name if isinstance(performer, Performer) else repr(performer)
        )
        stats = self.store.cache_stats()
        by_type = ", ".join(f"{k}={v}" for k, v in sorted(stats.by_type.items()))
        print_info(
            f"Finished Stash processing for {performer_name} "
            f"(cache: {stats.total_entries} entries — {by_type})"
        )

    async def _run_file_first(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None:
        """Run the file-first Stash processing flow for a creator.

        Sweeps the creator's Stash files, adjudicates each against the
        downloaded media (posts + messages), creates any split scenes, composes
        one gallery per owning item, flushes the rest in a single batched
        ``save_all``, and persists the stash ids to the metadata DB.

        Args:
            account: The Account being processed
            performer: The Performer for the account
            studio: Optional Studio to associate with galleries
        """
        (
            index,
            item_entities,
            media_with_id,
            split_pairs,
        ) = await self._prepare_file_first(account)
        # Sweep first: on a server that returns each file's scenes/images with
        # it, the store then already holds the entities the re-verify reads.
        swept = [file async for file in self._sweep_creator_files()]
        # Media already carrying a stash_id are re-verified by id and dropped
        # from the index, so their files are not adjudicated a second time.
        await self._fast_path_known_media(
            index, account, studio, item_entities, media_with_id, split_pairs
        )
        swept += await self._quoted_files_elsewhere(index, swept, account)
        for file in swept:
            await self._safe_adjudicate(
                file, index, account, studio, item_entities, media_with_id, split_pairs
            )
        await self._compose_and_flush(
            account, performer, studio, item_entities, media_with_id, split_pairs
        )

    async def _run_file_first_incremental(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None:
        """Sweep-free incremental file-first pass for the monitoring daemon.

        Builds the media index from just-downloaded content, scans exactly those
        files into Stash (settling before reads), then locates each media's file
        by basename (``find_one`` -> one row; snowflake-unique, override-safe)
        instead of a creator-wide sweep. Reuses the shared adjudication, gallery
        composition, and batched flush. Media whose file is not yet visible to
        Stash are left for a later cycle.

        Args:
            account: The Account being processed
            performer: The Performer for the account
            studio: Optional Studio to associate with galleries
        """
        (
            index,
            item_entities,
            media_with_id,
            split_pairs,
        ) = await self._prepare_file_first(account)
        # Scan exactly the just-downloaded files. local_path is set this run for
        # freshly-downloaded media; absent for prior-cycle media already scanned.
        scan_paths = sorted(
            {
                get_stash_path(Path(media.local_path), self.config)
                for media, _owners in index.values()
                if media.local_path
            }
        )
        if scan_paths:
            await self.scan_creator_folder(paths=scan_paths)
        await self._fast_path_known_media(
            index,
            account,
            studio,
            item_entities,
            media_with_id,
            split_pairs,
            current_only=True,
        )
        # Only this cycle's downloads (local_path is the marker), matched by
        # basename, NOT by path: under override_dldir_w_mapped the Stash library
        # is reorganized, so local paths do not correspond to Stash paths
        # (get_stash_path collapses to the mapped root). The basename carries
        # the snowflake media id, survives the reorg, and is the same key the
        # sweep's leaf-index uses. (Scanning above is by path to make Stash
        # ingest; identity matching is by basename.)
        current = [
            media.local_filename
            for media, _owners in index.values()
            if media.local_path and media.local_filename
        ]
        for file in await self._find_files_by_basename(current):
            await self._safe_adjudicate(
                file,
                index,
                account,
                studio,
                item_entities,
                media_with_id,
                split_pairs,
            )
        await self._compose_and_flush(
            account, performer, studio, item_entities, media_with_id, split_pairs
        )

    async def _quoted_files_elsewhere(
        self,
        index: dict[str, tuple[Media, list[Post | Message]]],
        swept: list[BaseFile],
        account: Account,
    ) -> list[BaseFile]:
        """Find quoted media whose file is outside this creator's folder.

        Quoted media is saved under its original poster when that folder
        exists, so the creator sweep does not see it.

        Args:
            index: Media index keyed by filename -> (Media, owning items)
            swept: Files the creator sweep returned
            account: The Account being processed

        Returns:
            The Stash files for indexed media of other accounts' posts that
            the sweep did not return.
        """
        seen = {
            PurePath(file.path).name
            for file in swept
            if not isinstance(file.path, UnsetType)
        }
        names = [
            leaf
            for leaf, (_media, owners) in index.items()
            if leaf not in seen
            and isinstance(owners[0], Post)
            and owners[0].accountId != account.id
        ]
        return await self._find_files_by_basename(names) if names else []

    async def _find_files_by_basename(self, names: list[str]) -> list[BaseFile]:
        """Fetch the Stash files with the given basenames, a page of names per query.

        Args:
            names: File basenames to look up

        Returns:
            One file per basename Stash has (the first when several share it).
        """
        batch = self.config.stash_files_batch
        found: dict[str, BaseFile] = {}
        for start in range(0, len(names), batch):
            pattern = "|".join(re.escape(name) for name in names[start : start + batch])
            async for file in self.store.find_iter(
                BaseFile, query_batch=batch, basename__regex=f"^({pattern})$"
            ):
                if not isinstance(file.path, UnsetType):
                    found.setdefault(PurePath(file.path).name, file)
        return list(found.values())

    async def _prepare_file_first(
        self, account: Account
    ) -> tuple[
        dict[str, tuple[Media, list[Post | Message]]],
        dict[int, tuple[Post | Message, list[Scene | Image]]],
        list[Media],
        list[tuple[Media, Scene]],
    ]:
        """Build the media index and the empty run-level accumulators.

        A cold preload (STASH_ONLY) resolves belongs_to/habtm but not has_many
        reverse-FK lists, so posts/messages load attachment-less; rebuild those
        lists before the gather filters on them.
        """
        self._reconstruct_attachment_lists()
        self._reconstruct_mention_lists()
        posts = await self._gather_creator_posts(account)
        messages = await self._gather_creator_messages(account)
        index = await self._build_media_index([*posts, *messages])
        # item.id -> (item, [Scene|Image, ...])
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]] = {}
        # Media that got a stash_id this run (owned scene / stamped image).
        media_with_id: list[Media] = []
        # (media, new_scene) — stash_id assigned once the scene is created.
        split_pairs: list[tuple[Media, Scene]] = []
        return index, item_entities, media_with_id, split_pairs

    async def _safe_adjudicate(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None:
        """Adjudicate one file with per-file isolation — one bad file never aborts."""
        try:
            await self._adjudicate_swept_file(
                file, index, account, studio, item_entities, media_with_id, split_pairs
            )
        except Exception as exc:
            file_id = getattr(file, "id", "?")
            logger.exception(f"Failed to adjudicate file {file_id}", exc_info=exc)
            debug_print(
                {
                    "method": "StashProcessing - run_file_first",
                    "status": "file_adjudication_failed",
                    "file_id": file_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    async def _adjudicate_swept_file(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None:
        """Adjudicate one swept file, accumulating its entities in place.

        Processes the file against the media index and, for any entities it
        yields, pairs them with the owning Media/item and records them in the
        run-level accumulators (``item_entities``, ``media_with_id``,
        ``split_pairs``) that the caller flushes once.

        Args:
            file: The swept Stash file to adjudicate
            index: Media index keyed by filename -> (Media, owning items)
            account: The Account being processed
            studio: Optional Studio to associate with entities
            item_entities: Accumulator: item.id -> (item, [Scene|Image, ...])
            media_with_id: Accumulator for Media that got a stash_id this run
            split_pairs: Accumulator for (media, new_scene) split pairs
        """
        if isinstance(file.path, UnsetType):  # sweep fragments always query path
            return
        entities = await self._process_file_first(file, index, account, studio)
        if not entities:
            return
        media, owners = index[PurePath(file.path).name]  # re-lookup to pair
        self._accumulate_entities(
            media, owners, entities, item_entities, media_with_id, split_pairs
        )

    @staticmethod
    def _flushed_stash_id(obj: Media | Post | Message) -> bool:
        """Whether ``obj`` carries a stash_id an earlier run's flush persisted.

        A stash_id set in memory but not yet saved (this run, or a run whose
        flush failed) does not count.
        """
        return (
            getattr(obj, "stash_id", None) is not None
            and "stash_id" not in obj.get_changed_fields()
        )

    @classmethod
    def _accumulate_entities(
        cls,
        media: Media,
        owners: list[Post | Message],
        entities: list[Scene | Image],
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
        *,
        synced: bool = False,
    ) -> None:
        """Fan a media's adjudicated entities into every owner's gallery, once.

        The entity joins EVERY owning item's gallery (a shared Media must not
        lose the other items' galleries), but stash_id accumulation is
        once-per-entity, not per-owner. With ``synced`` (the entities were
        already flushed for this media by an earlier run), an owner that also
        has a flushed gallery id is skipped: its gallery already holds them.
        """
        for item in owners:
            if item.id is None:  # owners are persisted rows; id is always set
                continue
            if synced and cls._flushed_stash_id(item):
                continue
            item_entities.setdefault(item.id, (item, []))[1].extend(entities)
        for entity in entities:
            if isinstance(entity, Scene) and entity.is_new():
                # A split — owned scenes are not new; stash_id set on create.
                split_pairs.append((media, entity))
            else:
                # Owned scene / stamped image already set media.stash_id.
                media_with_id.append(media)

    async def _fast_path_known_media(
        self,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
        *,
        current_only: bool = False,
    ) -> None:
        """Re-verify already-stamped media by id.

        A media that already carries a stash_id goes straight to its Scene/Image
        via ``_process_media_fast_path`` (store get + primary re-verify) instead
        of being adjudicated from its file. A leaf is dropped from the index
        only when the fast-path definitively handled it (returned entities); a
        stale/foreign/skip result leaves the leaf to be adjudicated from its
        file, so nothing is silently lost.

        Args:
            index: Media index keyed by filename -> (Media, owning items)
            account: The Account being processed
            studio: Optional Studio to associate with entities
            item_entities: Accumulator: item.id -> (item, [Scene|Image, ...])
            media_with_id: Accumulator for Media that got a stash_id this run
            split_pairs: Accumulator for (media, new_scene) split pairs
            current_only: Re-verify only media downloaded this cycle (those
                carrying ``local_path``), leaving the rest of the index alone
        """
        known = [
            (leaf, media, owners)
            for leaf, (media, owners) in list(index.items())
            if media.stash_id is not None and (media.local_path or not current_only)
        ]
        for leaf, media, owners in known:
            flushed_id = str(media.stash_id) if self._flushed_stash_id(media) else None
            entities = await self._process_media_fast_path(
                media, owners[0], account, studio
            )
            if not entities:
                continue  # stale/foreign/skip — leave in index for the sweep
            del index[leaf]  # definitively handled; do not re-adjudicate via sweep
            self._accumulate_entities(
                media,
                owners,
                entities,
                item_entities,
                media_with_id,
                split_pairs,
                # A demotion can hand back a new split scene instead.
                synced=all(entity.id == flushed_id for entity in entities),
            )

    async def _compose_and_flush(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None:
        """Compose one gallery per owning item, then flush once and persist.

        save_all is all-or-nothing; a batch failure re-raises so the creator is
        reported failed (not a clean success with stash_ids silently lost). The
        flow is idempotent — a failed creator self-heals on the next run.
        """
        await self._create_split_scenes(item_entities, split_pairs)
        if item_entities:
            # One set of bulk reads instead of three searches per item.
            await self._preload_galleries(
                [item for item, _entities in item_entities.values()], studio
            )
        for item, entities in item_entities.values():
            if not entities:
                continue  # its only entity was a split scene Stash refused
            # Per-item isolation: one bad compose must not abort the rest.
            try:
                await self._compose_gallery_for_item(
                    item, entities, account, performer, studio
                )
            except Exception as exc:
                print_error(f"Failed to compose gallery for item {item.id}: {exc}")
                logger.exception(
                    f"Failed to compose gallery for item {item.id}", exc_info=exc
                )
                debug_print(
                    {
                        "method": "StashProcessing - run_file_first",
                        "status": "gallery_compose_failed",
                        "item_id": item.id,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
        try:
            # Updates owned scenes/images and links galleries (links ride
            # save_all via gallery side-mutations).
            await self.store.save_all()

            # Persist affected Media rows to the FDNG metadata DB (NOT the Stash
            # store). Dedupe by object identity so each Media is saved once.
            fdng_store = get_store()
            affected = media_with_id + [m for m, _ in split_pairs]
            for media in {id(m): m for m in affected}.values():
                await fdng_store.save(media)
            # Gallery ids recorded while composing: later runs find the gallery
            # by id and skip items that are fully flushed.
            for item, _entities in item_entities.values():
                if "stash_id" in item.get_changed_fields():
                    await fdng_store.save(item)
        except Exception as exc:
            print_error(f"Failed to flush file-first batch: {exc}")
            logger.exception("Failed to flush file-first batch", exc_info=exc)
            debug_print(
                {
                    "method": "StashProcessing - run_file_first",
                    "status": "batch_flush_failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            raise

    async def _create_split_scenes(
        self,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None:
        """Create each split scene in Stash before anything links to it.

        Left to ``save_all``, a new scene that a gallery links is created twice
        (queued in the batch, then cascade-saved for the gallery), and Stash
        rejects the second create. A scene Stash refuses is dropped from this
        run so the rest of the creator still flushes.

        Args:
            item_entities: Accumulator: item.id -> (item, [Scene|Image, ...])
            split_pairs: (media, new_scene) pairs; refused scenes are removed
        """
        created: list[tuple[Media, Scene]] = []
        for media, scene in split_pairs:
            try:
                await self.store.save(scene)
            except Exception as exc:
                logger.exception(
                    f"Failed to create split scene for media {media.id}",
                    exc_info=exc,
                )
                self.store.invalidate(scene)
                for _item, entities in item_entities.values():
                    entities[:] = [e for e in entities if e is not scene]
                continue
            media.stash_id = int(scene.id)
            created.append((media, scene))
        split_pairs[:] = created

    async def _compose_gallery_for_item(
        self,
        item: Post | Message,
        entities: list[Scene | Image],
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None:
        """Get-or-create the item's gallery and link its adjudicated entities.

        Linking: hashtags become tags, Images ride ``gallery.images``
        (addGalleryImages side-mutation), Scenes are added via ``add_scene``.
        No explicit save here — the gallery is already dirty and its links flush
        on the run-level ``save_all``. A quoted post's gallery carries that
        post's author, not the creator being processed.

        Args:
            item: The owning Post or Message
            entities: Scene/Image entities adjudicated for this item
            account: The Account being processed
            performer: The Performer for the account
            studio: Optional Studio to associate with the gallery
        """
        creator = await self._item_creator(item, account, studio)
        if creator is None:
            return
        gallery_performer: Performer | None = performer
        if creator[0].id != account.id:
            account, studio = creator
            gallery_performer = await self._get_or_create_performer(account)
        if isinstance(item, Post):
            item_type = "post"
            url_pattern = f"https://fansly.com/post/{item.id}"
        else:
            item_type = "message"
            url_pattern = f"https://fansly.com/messages/{item.groupId}/{item.id}"

        gallery = await self._get_or_create_gallery(
            item=item,
            account=account,
            performer=gallery_performer,
            studio=studio,
            item_type=item_type,
            url_pattern=url_pattern,
        )
        if not gallery:
            return

        hashtags = getattr(item, "hashtags", None)
        if hashtags:
            for tag in await self._process_hashtags_to_tags(hashtags):
                await self._link_tag(gallery, tag)

        existing_images = gallery.images if isinstance(gallery.images, list) else []
        gallery.images = existing_images + [e for e in entities if isinstance(e, Image)]
        for scene in [e for e in entities if isinstance(e, Scene)]:
            await gallery.add_scene(scene)
