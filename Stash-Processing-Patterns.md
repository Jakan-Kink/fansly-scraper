# Stash Processing Patterns

This document outlines the patterns used in the Stash processing implementation, based on analysis of the actual code.

## Core Patterns

### 1. Protocol Usage Pattern

```python
@runtime_checkable
class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""
    content: str | None
    createdAt: datetime
    attachments: list[Attachment]
    # Messages don't have accountMentions, only Posts do
    accountMentions: list[Account] | None = None

# Used for type flexibility
async def _update_content_metadata(
    self,
    file: Scene | Image,
    content: HasMetadata,  # Can be Post or Message
    media_obj: AccountMedia,
    session: Session | None = None,
) -> None:
```

### 2. Instance Creation Pattern

```python
@dataclass
class StashProcessing:
    # Required fields without defaults
    config: FanslyConfig
    state: DownloadState
    stash_interface: StashInterface
    database: Database

    # Optional fields with defaults
    _background_task: asyncio.Task | None = None
    _cleanup_event: asyncio.Event | None = None
    _owns_db: bool = False

    @classmethod
    def from_config(cls, config: FanslyConfig, state: DownloadState) -> "StashProcessing":
        # Deep copy state to prevent modification
        state_copy = deepcopy(state)

        # Get interface from config
        stash_interface = config.get_stash_api()

        # Create database with same shared memory
        database = Database(config, creator_name=state.creator_name)
```

### 3. Processing Flow Pattern

```python
async def continue_stash_processing(
    self, account: Account | None, performer: Performer | None
) -> None:
    """Processing order:
    1. Update account stash_id
    2. Process studio
    3. Process posts
    4. Process messages
    """
    # 1. Update stash_id if needed
    if account.stash_id != performer.id:
        await self._update_account_stash_id(
            account=account,
            performer=performer,
        )

    # 2. Create/update studio
    studio = await self.process_creator_studio(
        account=account,
        performer=performer,
    )

    # 3. Process content
    await self.process_creator_posts(...)
    await self.process_creator_messages(...)
```

## Data Handling Patterns

### 4. Title Generation Pattern

```python
def _generate_title_from_content(
    self,
    content: str | None,
    username: str,
    created_at: datetime,
    current_pos: int | None = None,
    total_media: int | None = None,
) -> str:
    """Three-level title generation:
    1. First line of content (if 10-128 chars)
    2. Truncated content with ellipsis (if >128 chars)
    3. Fallback to date-based format
    """
    title = None
    if content:
        # Try to get first line as title
        first_line = content.split("\n")[0].strip()
        if len(first_line) >= 10 and len(first_line) <= 128:
            title = first_line
        elif len(first_line) > 128:
            title = first_line[:125] + "..."

    # If no suitable title from content, use date format
    if not title:
        title = f"{username} - {created_at.strftime('%Y/%m/%d')}"

    # Append position if multiple media
    if total_media and total_media > 1 and current_pos:
        title = f"{title} - {current_pos}/{total_media}"
```

### 5. Media Processing Pattern

```python
# Media is processed in a specific order:

# 1. Preview First (if exists)
if await media.awaitable_attrs.preview:
    stash_obj, file = await self._process_media_file(
        media.preview,
        media,
        is_preview=True
    )
    if stash_obj and file:
        await self._update_file_metadata(
            file=file,
            media_obj=media,
            is_preview=True,
            session=session,
        )
        files.append(file)

# 2. Main Media
if await media.awaitable_attrs.media:
    stash_obj, file = await self._process_media_file(
        media.media,
        media
    )
    if stash_obj and file:
        await self._update_file_metadata(
            file=file,
            media_obj=media,
            session=session
        )
        files.append(file)
```

### 6. File Type Pattern

```python
# Pattern matching on MIME types for file handling
match mime_type:
    case str() as mime if mime.startswith("image"):
        stash_obj = self.stash_interface.find_image(stash_id)[0]
        return (stash_obj, Image.from_dict(stash_obj))
    case str() as mime if mime.startswith("video"):
        stash_obj = self.stash_interface.find_scene(stash_id)[0]
        return (stash_obj, Scene.from_dict(stash_obj))
    case str() as mime if mime.startswith("application"):
        # Handle streaming formats
        stash_obj = self.stash_interface.find_scene(stash_id)[0]
        return (stash_obj, Scene.from_dict(stash_obj))
    case _:
        raise ValueError(f"Invalid media type: {mime_type}")
```

## Organization Patterns

### 7. Studio Hierarchy Pattern

```python
# Two-level studio hierarchy:

# 1. Network Level
fansly_studio_dict = self.stash_interface.find_studio("Fansly (network)")
fansly_studio = Studio.from_dict(
    fansly_studio_dict.get("findStudio", fansly_studio_dict)
)

# 2. Creator Level
creator_studio_name = f"{account.username} (Fansly)"
studio_data = self.stash_interface.find_studio(creator_studio_name)
if studio_data is None:
    # Create new studio under network
    studio = Studio(
        id="new",
        name=creator_studio_name,
        parent_studio=fansly_studio,
        url=f"https://fansly.com/{account.username}",
    )
```

### 8. Attachment Processing Pattern

```python
# Attachments are processed in three possible ways:

# 1. Direct Media
if await attachment.awaitable_attrs.media:
    media: AccountMedia = attachment.media
    files.extend(
        await self._process_media_to_files(media=media, session=session)
    )

# 2. Media Bundles
if await attachment.awaitable_attrs.bundle:
    bundle: AccountMediaBundle = attachment.bundle
    # Get ordered media from bundle
    bundle_media = await session.execute(
        select(AccountMedia)
        .join(
            account_media_bundle_media,
            AccountMedia.id == account_media_bundle_media.c.media_id,
        )
        .where(account_media_bundle_media.c.bundle_id == bundle.id)
        .order_by(account_media_bundle_media.c.pos)  # Maintain order
    )
    bundle_media = bundle_media.scalars().all()
    for media in bundle_media:
        files.extend(
            await self._process_media_to_files(media=media, session=session)
        )

# 3. Aggregated Posts
if attachment.is_aggregated_post:
    # Handle posts that are collections of other posts
```

## Infrastructure Patterns

### 9. Logging Pattern

```python
# Multi-level logging setup:

# 1. File Logging with Rotation
file_handler = SizeAndTimeRotatingFileHandler(
    filename=str(log_file),
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    when="h",  # Hourly rotation
    interval=1,
    utc=True,
    compression="gz",
    keep_uncompressed=2,  # Keep 2 most recent logs uncompressed
)

# 2. Console Logging
console_handler = logging.StreamHandler(sys.stdout)
console_formatter = logging.Formatter("%(levelname)s: %(message)s")

# 3. Debug Object Printing
def debug_print(obj):
    """Debug printing with proper formatting."""
    try:
        formatted = pformat(obj, indent=2)
        logger.debug(formatted)
        for handler in logger.handlers:
            handler.flush()
```

### 10. Error Handling Pattern

```python
# Multiple levels of error handling:

# 1. Individual Item Processing
try:
    await self._process_items_with_gallery(...)
except Exception as e:
    print_error(f"Failed to process {item_type} {item.id}: {e}")
    debug_print({
        "method": f"StashProcessing - process_creator_{item_type}s",
        "error": str(e),
        "traceback": traceback.format_exc(),
    })
    continue  # Skip failed item, continue with others

# 2. Resource Cleanup
try:
    # Process items
finally:
    # Always cleanup resources
    if hasattr(session, "close"):
        try:
            await session.close()
        except Exception as close_error:
            print_error(f"Error closing session: {close_error}")
```

### 11. Cleanup Pattern

```python
async def cleanup(self) -> None:
    """Safely cleanup resources in order:
    1. Background tasks
    2. Database
    3. Logging
    """
    try:
        # 1. Cancel and wait for background task
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            if self._cleanup_event:
                await self._cleanup_event.wait()

        # 2. Database cleanup (only if we own it)
        if self._owns_db and self.database is not None:
            # Ensure all processing is done
            if hasattr(self.database, "optimized_storage"):
                # Commit pending transactions
                with self.database.get_sync_session() as session:
                    session.commit()
                # Final sync
                self.database.optimized_storage.sync_manager.sync_now()
            await self.database.cleanup()

    finally:
        # 3. Always cleanup logging
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
```

### 12. Session and Batch Processing Pattern

```python
# Sessions are handled with context managers
async with self.database.get_async_session() as session:
    # Complex queries use joins and eager loading
    stmt = (
        select(Group)
        .join(Group.users)
        .join(Group.messages)
        .join(Message.attachments)
        .where(Group.users.any(Account.id == account.id))
    )

    # Batch processing for performance
    batch_size = 15  # One timeline page worth
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        try:
            await self._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="message",
                items=batch,
                url_pattern_func=get_message_url,
            )
```

### 13. Scan Configuration Pattern

```python
# Scan setup with all preview generation enabled
scan_metadata_input = {
    "rescan": False,  # Don't rescan existing
    "scanGenerateCovers": True,      # Gallery/scene covers
    "scanGeneratePreviews": True,    # Video previews
    "scanGenerateThumbnails": True,  # Image thumbnails
    "scanGenerateImagePreviews": True,  # Preview images
    "scanGenerateSprites": True,     # Video sprites
    "scanGeneratePhashes": True,     # Perceptual hashes
    "scanGenerateClipPreviews": True,  # Clip previews
}

# Path handling
if not self.state.download_path:
    self.state.download_path = set_create_directory_for_download(
        self.config, self.state
    )
```

## Query Building Patterns

### 14. Relationship Loading Pattern

```python
# Complex joins with relationship traversal
stmt = (
    select(Group)
    .join(Group.users)        # Follow relationship to users
    .join(Group.messages)     # Follow relationship to messages
    .join(Message.attachments)  # Follow relationship to attachments
    .where(Group.users.any(Account.id == account.id))  # Filter on nested relationship
)

# Lazy loading with awaitable attributes
messages = await group.awaitable_attrs.messages
messages_with_attachments = [m for m in messages if m.attachments]

# Eager loading with selectinload
stmt = (
    select(Post)
    .join(Post.attachments)  # Required join
    .where(Post.accountId == account.id)
    .options(
        selectinload(Post.attachments),     # Load full relationship
        selectinload(Post.accountMentions),  # Load additional relationship
    )
)
```

### 15. Query Result Pattern

```python
# Multiple result handling patterns:

# 1. Single Optional Result
result = await session.execute(stmt)
account = result.scalar_one_or_none()  # Returns object or None

# 2. List Results
result = await session.execute(stmt)
groups = result.scalars().all()  # Returns list of objects

# 3. Filtered Results
messages_with_attachments = [
    m for m in messages
    if m.attachments  # Filter after load
]

# 4. Batch Processing Results
batch_size = 15  # One timeline page worth
for i in range(0, len(items), batch_size):
    batch = items[i : i + batch_size]
    try:
        await self._process_items_with_gallery(...)
```

### 16. Transaction Pattern

```python
# Multiple transaction levels:

# 1. Session-level Transaction
async with self.database.get_async_session() as session:
    # Everything in this block is in a transaction
    stmt = select(Group).join(Group.users)
    result = await session.execute(stmt)
    # Auto-commit on success, rollback on error

# 2. Nested Transaction
async with session.begin_nested():
    # Process all variant media items
    for variant in new_variants:
        await _process_media_item_dict_inner(...)

    # Batch insert all variant relationships
    if new_variants:
        await session.execute(
            media_variants.insert()
            .prefix_with("OR IGNORE")
            .values([...])
        )

# 3. Explicit Transaction Control
try:
    # Process items
    if session.is_active:
        await session.commit()
except Exception:
    if session.is_active:
        try:
            await session.rollback()
        except Exception:
            pass  # Ignore rollback errors
```

### 17. Media Variant Pattern

```python
# Media file lookup hierarchy:

# 1. Try stash_id first
if media_file.stash_id:
    return self._find_stash_file_by_id(
        media_file.stash_id,
        media_file.mimetype,
    )

# 2. Try local file or variants
elif media_file.local_filename or await media_file.awaitable_attrs.variants:
    return await self._find_stash_file_by_path(
        media_file,
        media_file.mimetype,
    )

# Process both preview and main media
if await media.awaitable_attrs.preview:
    # Handle preview first
    stash_obj, file = await self._process_media_file(
        media.preview,
        media,
        is_preview=True
    )

if await media.awaitable_attrs.media:
    # Then handle main media
    stash_obj, file = await self._process_media_file(
        media.media,
        media
    )
```

### 18. Background Task Pattern

```python
# Task Creation and Management

# 1. Create and Track Task
loop = asyncio.get_running_loop()
self._background_task = loop.create_task(
    self._safe_background_processing(account, performer)
)
self.config._background_tasks.append(self._background_task)

# 2. Safe Background Processing
async def _safe_background_processing(
    self, account: Account | None, performer: Performer | None
) -> None:
    try:
        await self.continue_stash_processing(account, performer)
    except asyncio.CancelledError:
        debug_print({"status": "background_task_cancelled"})
        raise
    except Exception as e:
        debug_print({
            "error": f"background_task_failed: {e}",
            "traceback": traceback.format_exc(),
        })
        raise
    finally:
        if self._cleanup_event:
            self._cleanup_event.set()

# 3. Task Cleanup
if self._background_task and not self._background_task.done():
    self._background_task.cancel()
    if self._cleanup_event:
        await self._cleanup_event.wait()
```
