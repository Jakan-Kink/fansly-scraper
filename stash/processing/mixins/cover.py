"""Cover images for galleries that hold only videos.

Stash takes a gallery's cover from an image in the gallery, so a post with
only videos has none. Each such post gets one JPEG in a ``Covers`` folder
beside its first video: Fansly's own poster when the video was downloaded in
this run, otherwise the video's first frame.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePath

import av
from stash_graphql_client import is_set
from stash_graphql_client.types import (
    BaseFile,
    Image,
    ImageFile,
    Scene,
    Studio,
    UnsetType,
    VideoFile,
)

from config.modes import DownloadMode
from download.media import _download_file
from fileio.normalize import COVERS_DIR, cover_name
from media.media import _get_best_location_url
from metadata import Account, Media, Message, Post
from pathio import get_local_path

from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


# Fansly's media type for a still image; a video's poster is a JPEG of it.
_IMAGE_TYPE = 1


def _save_first_frame(video: Path, cover: Path) -> None:
    """Save a video's first frame as a JPEG."""
    with av.open(str(video)) as container:
        frame = next(container.decode(video=0))
        frame.to_image().save(cover, format="JPEG", quality=90)


class CoverProcessingMixin(StashProcessingProtocol):
    """Creates cover images and links them to their galleries."""

    def _covers_wanted(
        self,
        index: dict[str, tuple[Media, list[Post | Message]]],
    ) -> dict[str, tuple[Media, dict[int, Post | Message]]]:
        """Pick the video each image-less post or message takes its cover from.

        Args:
            index: Media index keyed by filename -> (Media, owning items)

        Returns:
            Cover file name -> (the video, the items it is the cover for by
            id); empty when ``stash_context.gallery_covers`` is off.
        """
        if not self.config.stash_gallery_covers:
            return {}
        by_item: dict[int, tuple[Post | Message, list[Media]]] = {}
        for media, owners in index.values():
            for item in owners:
                if item.id is not None:
                    by_item.setdefault(item.id, (item, []))[1].append(media)
        wanted: dict[str, tuple[Media, dict[int, Post | Message]]] = {}
        for item_id, (item, medias) in by_item.items():
            if any((m.mimetype or "").startswith("image/") for m in medias):
                continue
            videos = sorted(
                (m for m in medias if (m.mimetype or "").startswith("video/")),
                key=lambda m: m.id or 0,
            )
            if videos:
                leaf = cover_name(videos[0].local_filename or "")
                wanted.setdefault(leaf, (videos[0], {}))[1][item_id] = item
        return wanted

    def _local_video_paths(self, files: list[BaseFile]) -> dict[str, Path]:
        """Where the videos Stash lists sit on this computer.

        Args:
            files: Stash files found for the creator

        Returns:
            Video file name -> local path, for the files whose Stash path can
            be translated back.
        """
        paths: dict[str, Path] = {}
        for file in files:
            if isinstance(file, VideoFile) and not isinstance(file.path, UnsetType):
                local = get_local_path(file.path, self.config)
                if local is not None:
                    paths.setdefault(local.name, local)
        return paths

    async def _ensure_covers(
        self,
        wanted: dict[str, tuple[Media, dict[int, Post | Message]]],
        locations: dict[str, Path],
    ) -> list[Path]:
        """Create the cover files that do not exist yet.

        Args:
            wanted: Cover file name -> (the video, the items it covers)
            locations: Video file name -> local path, for videos not
                downloaded in this run

        Returns:
            The cover files written by this call.
        """
        created: list[Path] = []
        for leaf, (video, _items) in wanted.items():
            video_path = (
                Path(video.local_path)
                if video.local_path
                else locations.get(video.local_filename or "")
            )
            if video_path is None or not await asyncio.to_thread(video_path.is_file):
                continue
            cover = video_path.parent / COVERS_DIR / leaf
            if await asyncio.to_thread(cover.exists):
                continue
            await asyncio.to_thread(cover.parent.mkdir, exist_ok=True)
            if not await self._download_poster(video, cover):
                try:
                    await asyncio.to_thread(_save_first_frame, video_path, cover)
                except Exception as exc:
                    logger.warning(f"No cover made for {video_path.name}: {exc}")
                    await asyncio.to_thread(cover.unlink, missing_ok=True)
                    continue
            created.append(cover)
        return created

    async def _download_poster(self, video: Media, cover: Path) -> bool:
        """Save Fansly's poster for a video downloaded in this run.

        Only this run's downloads carry a link that is still valid, and
        stash-only mode does not contact Fansly at all.

        Args:
            video: The video's Media
            cover: Where the poster is saved

        Returns:
            True when the poster was saved.
        """
        if self.config.download_mode == DownloadMode.STASH_ONLY or not video.local_path:
            return False
        poster = next(
            (
                variant
                for variant in video.variants
                if variant.mimetype == "image/jpeg" and variant.type == _IMAGE_TYPE
            ),
            None,
        )
        url = _get_best_location_url(poster) if poster is not None else None
        if not url:
            return False
        try:
            with cover.open("wb") as output:
                await _download_file(self.config, url, output)
        except Exception as exc:
            logger.debug(f"Poster for media {video.id} not downloaded: {exc}")
            await asyncio.to_thread(cover.unlink, missing_ok=True)
            return False
        return True

    @staticmethod
    def _in_a_gallery(image: Image) -> bool:
        """Whether Stash already lists the image in a gallery."""
        return is_set(image.galleries) and bool(image.galleries)

    async def _link_covers(
        self,
        files: list[BaseFile],
        wanted: dict[str, tuple[Media, dict[int, Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
    ) -> None:
        """Hand each new cover image to the galleries it is the cover for.

        A cover already in a gallery was linked by an earlier run and is left
        alone. The image joins ``item_entities`` so the gallery step adds it,
        and ``_covers`` so the cover is set once the gallery holds it.

        Args:
            files: Stash files found for the creator
            wanted: Cover file name -> (the video, the items it covers)
            account: The Account being processed
            studio: Its pre-resolved studio
            item_entities: Accumulator: item.id -> (item, entities)
        """
        for file in files:
            if not isinstance(file, ImageFile) or isinstance(file.path, UnsetType):
                continue
            path = PurePath(file.path)
            entry = wanted.get(path.name)
            if entry is None or path.parent.name != COVERS_DIR:
                continue
            video, items = entry
            image_file = (
                file
                if is_set(file.images)
                else await self.store.populate(file, ["images"])
            )
            images = image_file.images if is_set(image_file.images) else None
            if not images or self._in_a_gallery(images[0]):
                continue  # checked before the re-read: a linked cover costs nothing
            image = await self._complete_entity(images[0])
            if image is None or self._in_a_gallery(image):
                continue
            await self._stamp_unposted(image, video, account, studio)
            await self._link_tag(image, await self._get_or_create_tag("Cover"))
            for item_id, item in items.items():
                item_entities.setdefault(item_id, (item, []))[1].append(image)
                self._covers[item_id] = image
