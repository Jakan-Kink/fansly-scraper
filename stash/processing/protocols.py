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
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session
    from stash_graphql_client import ServerCapabilities, StashContext
    from stash_graphql_client.store import StashEntityStore
    from stash_graphql_client.types import (
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
    from metadata import Account, Attachment, Database, Media, Message, Post


class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""

    id: int
    content: str | None
    createdAt: datetime
    attachments: list[Any]
    accountMentions: list[Account] | None
    stash_id: int | None
    awaitable_attrs: Callable | None


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
    database: Database
    log: logging.Logger
    _background_task: asyncio.Task | None
    _cleanup_event: asyncio.Event
    _owns_db: bool

    # Per-creator cached lookups
    _account: Account | None
    _performer: Performer | None
    _studio: Studio | None
    _stash_parent_task: str | None

    # Media code indexes for O(1) lookups
    _scene_code_index: dict[str, list[Scene]]
    _image_code_index: dict[str, list[Image]]

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

    def find_scenes_by_media_codes(
        self, media_codes: list[str]
    ) -> dict[str, list[Scene]]: ...

    def find_images_by_media_codes(
        self, media_codes: list[str]
    ) -> dict[str, list[Image]]: ...

    # --- AccountProcessingMixin methods ---

    async def process_creator(
        self, session: Session | None = None
    ) -> tuple[Account, Performer]: ...

    async def _find_existing_performer(self, account: Account) -> Performer | None: ...

    async def _get_or_create_performer(self, account: Account) -> Performer: ...

    async def _update_account_stash_id(
        self,
        account: Account,
        performer: Performer,
        session: AsyncSession | None = None,
    ) -> None: ...

    # --- StudioProcessingMixin methods ---

    async def _find_existing_studio(self, account: Account) -> Studio | None: ...

    async def process_creator_studio(
        self,
        account: Account,
        session: Session | None = None,
    ) -> Studio | None: ...

    # --- TagProcessingMixin methods ---

    async def _process_hashtags_to_tags(self, hashtags: list[Any]) -> list[Tag]: ...

    async def _add_preview_tag(self, file: Scene | Image) -> None: ...

    # --- MediaProcessingMixin methods ---

    def _get_file_from_stash_obj(
        self, stash_obj: Scene | Image
    ) -> ImageFile | VideoFile | None: ...

    async def _find_stash_files_by_path(
        self, media_files: list[tuple[str, str]]
    ) -> list[tuple[dict, Scene | Image]]: ...

    async def _find_stash_files_by_id(
        self,
        stash_files: list[tuple[str | int, str]],
        session: Session | None = None,
    ) -> list[tuple[dict, Scene | Image]]: ...

    async def _process_media_batch_by_mimetype(
        self,
        media_list: Sequence[Media],
        item: Any,
        account: Account,
    ) -> dict[str, list[Image | Scene]]: ...

    def _chunk_list(
        self, items: Sequence[str], chunk_size: int
    ) -> list[Sequence[str]]: ...

    # --- GalleryProcessingMixin methods ---

    async def _process_item_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Any,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
        session: Session | None = None,
    ) -> None: ...

    async def _has_media_content(self, item: HasMetadata) -> bool: ...

    # --- ContentProcessingMixin methods ---

    async def _collect_media_from_attachments(
        self, attachments: list[Attachment]
    ) -> list[Media]: ...

    async def _process_items_with_gallery(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        items: list[Message | Post],
        url_pattern_func: Callable,
        session: Session | None = None,
    ) -> None: ...

    # --- BatchProcessingMixin methods ---

    async def _setup_worker_pool(
        self, items: list[Any], item_type: str
    ) -> tuple[str, str, asyncio.Semaphore, asyncio.Queue]: ...

    async def _run_worker_pool(
        self,
        items: list[Any],
        task_name: str,
        process_name: str,
        semaphore: asyncio.Semaphore,
        queue: asyncio.Queue,
        process_item: Callable,
    ) -> None: ...

    # --- StashProcessing composed class methods ---

    async def continue_stash_processing(
        self,
        account: Account | None,
        performer: Performer | None,
        session: AsyncSession | None = None,
    ) -> None: ...
