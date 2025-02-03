# Fansly to Stash Field Transformations

## Direct Mappings

### Account → Performer

```python
class Account:
    id: int                  # Used for external reference
    username: str            # → name (if no displayName) or disambiguation
    displayName: str | None  # → name (if present)
    about: str | None       # → details (append to)
    location: str | None    # → country
    avatar: Media | None    # → image (requires download and path mapping)
    createdAt: int         # → details (include in formatted text)

class Performer:
    name: str              # ← displayName or username
    disambiguation: str    # ← username (if displayName used for name)
    details: str          # ← formatted: about + creation date + source info
    country: str          # ← location
    image: str            # ← downloaded avatar path
    url: str              # ← f"https://fansly.com/{username}/posts"
    # Not used:
    favorite: bool = False
    ignore_auto_tag: bool = False
    death_date: str | None = None
    career_length: str | None = None
```

### AccountMediaBundle → Gallery

```python
class AccountMediaBundle:
    id: int               # Used for external reference
    accountId: int        # → performer_ids[0] (via Account→Performer mapping)
    title: str | None     # → title
    description: str | None # → details (part of)
    createdAt: int        # → date (converted to ISO 8601)
    media: list[Media]    # → files (downloaded and path mapped)

class Gallery:
    title: str            # ← title or generated from content
    details: str          # ← formatted: description + metadata
    date: str            # ← createdAt (ISO 8601)
    url: str             # ← f"https://fansly.com/{username}/posts/{id}"
    performer_ids: list[int] # ← [accountId] (mapped to Performer)
    studio_id: int       # ← from config.user_names current creator
    files: list[str]     # ← downloaded media paths
```

## Complex Mappings

### Post → Scene (Single Video)

```python
class Post:
    id: int              # Used for external reference
    accountId: int       # → performer_ids[0] (via Account→Performer mapping)
    content: str | None  # → details (part of)
    createdAt: int      # → date (ISO 8601)
    attachments: list[Attachment] # → path (via Media download)

class Scene:
    title: str          # ← Generated from content or defaults
    details: str        # ← Formatted: content + metadata + source
    date: str          # ← createdAt (ISO 8601)
    url: str           # ← f"https://fansly.com/{username}/posts/{id}"
    path: str          # ← Downloaded video path
    performer_ids: list[int] # ← [accountId] + mentions
    studio_id: int     # ← from config.user_names current creator
    file_mod_time: str # ← From downloaded file
    height: int | None # ← From video metadata
    width: int | None  # ← From video metadata
    framerate: float | None # ← From video metadata
    bitrate: int | None # ← From video metadata
```

### Post → Gallery (Multiple Media)

```python
class Post:
    id: int              # Used for external reference
    accountId: int       # → performer_ids[0]
    content: str | None  # → details (part of)
    createdAt: int      # → date (ISO 8601)
    attachments: list[Attachment] # → files (via Media downloads)

class Gallery:
    title: str          # ← Generated from content
    details: str        # ← Formatted: content + metadata
    date: str          # ← createdAt (ISO 8601)
    url: str           # ← f"https://fansly.com/{username}/posts/{id}"
    performer_ids: list[int] # ← [accountId] + mentions
    studio_id: int     # ← from config.user_names current creator
    files: list[str]   # ← Downloaded media paths
```

### Wall → Gallery with Chapters

```python
class Wall:
    id: int              # Used for external reference
    accountId: int       # → performer_ids[0]
    description: str | None # → details (part of)
    posts: list[Post]    # → chapters (via GalleryChapter)

class GalleryChapter:
    title: str          # ← Generated from post content
    image_index: int    # ← First image from post
    # Each post's media becomes part of gallery.files
```

### Hashtag → Tag

```python
class Hashtag:
    id: int             # Used for external reference
    value: str          # → name (formatted) and aliases

class Tag:
    name: str           # ← Formatted hashtag value
    aliases: list[str]  # ← Original + variations
    description: str | None # ← Optional context
    parent_ids: list[int] # ← For hierarchical tags (manual)
```

## Special Cases

### Media Variants

- Choose highest quality for download
- Skip streaming variants (HLS/DASH)
- Store thumbnails if needed
- Handle both image and video metadata

### Post Mentions

- Add mentioned performers to Scene/Gallery
- Store as relationship for future updates
- Handle missing/unresolved mentions

### Wall Posts Organization

1. Wall becomes Gallery
2. Each Post becomes Chapter
3. All media from Posts in files
4. Maintain order and grouping

### Studio Assignment

- Based on config.user_names entry
- All content from creator goes to their Studio
- Helps organize content by source

### File Management

1. Download to configured paths
2. Maintain original names where possible
3. Handle duplicates
4. Track downloaded state

## Field Transformations

### Timestamps

```python
def convert_timestamp(ts: int) -> str:
    """Convert Fansly timestamp to ISO 8601 date."""
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.date().isoformat()
```

### Content Formatting

```python
def format_details(
    content: str | None,
    source_url: str,
    created_at: int,
    metadata: dict | None = None,
) -> str:
    """Format content and metadata as details."""
    parts = [
        f"Source: {source_url}",
        f"Created: {convert_timestamp(created_at)}",
    ]

    if content:
        parts.extend(["", strip_html(content)])

    if metadata:
        parts.extend(["", "Metadata:"])
        parts.extend(f"- {k}: {v}" for k, v in metadata.items())

    return "\n".join(parts)
```

### Tag Formatting

```python
def format_tag(value: str) -> tuple[str, list[str]]:
    """Format hashtag as tag name and aliases."""
    # Original form becomes alias
    aliases = [value]

    # Split and capitalize words
    words = re.findall(r'[A-Z]?[a-z]+|\d+', value)
    name = ' '.join(w.capitalize() for w in words)

    # Add common variations
    aliases.append(name.replace(' ', ''))

    return name, aliases
```

### URL Generation

```python
def get_fansly_url(username: str, type_: str, id_: str) -> str:
    """Generate Fansly URL."""
    return f"https://fansly.com/{username}/{type_}/{id_}"
```
