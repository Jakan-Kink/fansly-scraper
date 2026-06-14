"""Protocol definitions for StashProcessing mixins.

Defines the interface that all processing mixins can rely on,
following the same pattern as stash-graphql-client's StashClientProtocol.
Mixins inherit from this Protocol so Pylance resolves cross-mixin and
base-class attribute accesses without errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import AsyncIterator, Sequence
    from datetime import datetime
    from typing import Any

    from stash_graphql_client import ServerCapabilities, StashContext
    from stash_graphql_client.store import StashEntityStore
    from stash_graphql_client.types import (
        BaseFile,
        Gallery,
        Image,
        ImageFile,
        Performer,
        Scene,
        Studio,
        Tag,
        VideoFile,
    )

    from config import FanslyConfig
    from download.core import DownloadState
    from metadata import (
        Account,
        Attachment,
        Database,
        Media,
        Message,
        Post,
    )


class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash (Post / Message).

    Read-only members are properties at the widest type — Post and Message
    differ in nullability (e.g. ``Message.content: str`` vs
    ``Post.content: str | None``), and attribute members are invariant, so
    plain attributes could never match both models.
    """

    @property
    def id(self) -> int | None: ...

    @property
    def content(self) -> str | None: ...

    @property
    def createdAt(self) -> datetime | None: ...  # noqa: N802 - mirrors model camelCase field

    # Written through the protocol (gallery lookups stamp stash_id back).
    stash_id: int | None
    attachments: list[Attachment]


class StashProcessingProtocol(Protocol):
    """Protocol defining the interface expected by StashProcessing mixins.

    This protocol declares all attributes and methods that mixin classes
    can expect to be available on the composed StashProcessing instance.
    Includes base class attributes, properties, and cross-mixin methods.
    """

    # --- Base class attributes (from StashProcessingBase.__init__) ---

    config: FanslyConfig
    state: DownloadState
    context: StashContext
    database: Database | None
    log: logging.Logger
    _background_task: asyncio.Task | None
    _cleanup_event: asyncio.Event
    _owns_db: bool

    # Per-creator cached lookups
    _account: Account | None
    _performer: Performer | None
    _studio: Studio | None
    _stash_parent_task: str | None

    # --- Base class properties ---

    @property
    def store(self) -> StashEntityStore: ...

    @property
    def capabilities(self) -> ServerCapabilities: ...

    # --- Base class methods ---

    def _generate_title_from_content(
        self,
        content: str | None,
        username: str,
        created_at: datetime,
        current_pos: int | None = None,
        total_media: int | None = None,
    ) -> str: ...

    async def _build_media_index(
        self, items: Sequence[Post | Message]
    ) -> dict[str, tuple[Media, list[Post | Message]]]: ...

    def _sweep_creator_files(self) -> AsyncIterator[BaseFile]: ...

    async def _connect_stash(self) -> bool: ...

    async def scan_creator_folder(self, paths: list[str] | None = None) -> None: ...

    # --- AccountProcessingMixin methods ---

    async def process_creator(self) -> tuple[Account, Performer]: ...

    async def _find_existing_performer(self, account: Account) -> Performer | None: ...

    async def _get_or_create_performer(self, account: Account) -> Performer: ...

    async def _update_account_stash_id(
        self,
        account: Account,
        performer: Performer,
    ) -> None: ...

    # --- StudioProcessingMixin methods ---

    async def _find_existing_studio(self, account: Account) -> Studio | None: ...

    async def process_creator_studio(
        self,
        account: Account,
    ) -> Studio | None: ...

    # --- TagProcessingMixin methods ---

    async def _process_hashtags_to_tags(self, hashtags: list[Any]) -> list[Tag]: ...

    async def _add_preview_tag(self, file: Scene | Image) -> None: ...

    # --- MediaProcessingMixin methods ---

    async def _owned_scene(self, file: VideoFile) -> Scene | None: ...

    def _image_files_all_local(self, image: Image) -> bool: ...

    async def _split_scene_for_file(
        self,
        file: BaseFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> Scene: ...

    async def _process_file_first(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]: ...

    async def _adjudicate_image(
        self,
        file: ImageFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]: ...

    async def _adjudicate_not_owned(
        self,
        file: VideoFile,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]: ...

    async def _process_media_fast_path(
        self,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]: ...

    async def _fast_path_image(
        self,
        entity: Image,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> list[Scene | Image]: ...

    async def _stamp_metadata(
        self,
        stash_obj: Scene | Image,
        media: Media,
        item: Post | Message,
        account: Account,
        studio: Studio | None = None,
    ) -> None: ...

    # --- GalleryProcessingMixin methods ---

    async def _get_or_create_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
    ) -> Gallery | None: ...

    async def _has_media_content(self, item: HasMetadata) -> bool: ...

    # --- ContentProcessingMixin methods ---

    def _reconstruct_attachment_lists(self) -> None: ...

    def _reconstruct_mention_lists(self) -> None: ...

    async def _gather_creator_posts(self, account: Account) -> list[Post]: ...

    async def _gather_creator_messages(self, account: Account) -> list[Message]: ...

    async def _collect_media_from_attachments(
        self, attachments: list[Attachment]
    ) -> list[Media]: ...

    # --- StashProcessing composed class methods ---

    async def continue_stash_processing(
        self,
        account: Account | None,
        performer: Performer | None,
    ) -> None: ...

    async def _run_file_first(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None: ...

    async def _adjudicate_swept_file(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None: ...

    async def _fast_path_known_media(
        self,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None: ...

    @staticmethod
    def _accumulate_entities(
        media: Media,
        owners: list[Post | Message],
        entities: list[Scene | Image],
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None: ...

    async def _compose_gallery_for_item(
        self,
        item: Post | Message,
        entities: list[Scene | Image],
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None: ...

    async def process_creator_incremental(self) -> None: ...

    def _finalize_creator(self, performer: Performer | None) -> None: ...

    async def _run_file_first_incremental(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
    ) -> None: ...

    async def _prepare_file_first(
        self, account: Account
    ) -> tuple[
        dict[str, tuple[Media, list[Post | Message]]],
        dict[int, tuple[Post | Message, list[Scene | Image]]],
        list[Media],
        list[tuple[Media, Scene]],
    ]: ...

    async def _safe_adjudicate(
        self,
        file: BaseFile,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None: ...

    async def _compose_and_flush(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_entities: dict[int, tuple[Post | Message, list[Scene | Image]]],
        media_with_id: list[Media],
        split_pairs: list[tuple[Media, Scene]],
    ) -> None: ...
