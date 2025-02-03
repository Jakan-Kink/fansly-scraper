# Stash API Client Library

A Python library for interacting with the Stash GraphQL API, providing a high-level, type-safe interface using dataclasses.

## Overview

This library provides a clean, Pythonic interface to the Stash GraphQL API. It uses dataclasses to represent Stash objects, providing type safety and easy serialization/deserialization.

## Key Features

- Type-safe interface using Python dataclasses
- Full GraphQL schema coverage
- Automatic date/time handling
- Proper relationship management
- Batch operations support
- Protocol-based interface definitions

## Installation

```bash
pip install stashapp-api
```

## Basic Usage

```python
from stashapi.stashapp import StashInterface
from stash import Scene, Performer, Studio, Tag

# Create a StashInterface instance
interface = StashInterface("http://localhost:9999", "YOUR_API_KEY")

# Find a scene by ID
scene = Scene.find("123", interface)

# Update scene attributes
scene.title = "New Title"
scene.rating100 = 80
scene.save(interface)

# Find all scenes with a filter
scenes = Scene.find_all(
    interface,
    filter={"per_page": 10, "sort": "created_at"},
    q="tag:example"
)

# Create a new performer
performer = Performer(
    id="new",
    name="Example Performer",
    gender="FEMALE",
    birthdate=datetime(1990, 1, 1),
    urls=["https://example.com"]
)
performer.stash_create(interface)

# Batch update multiple scenes
Scene.update_batch(interface, scenes)
```

## Main Classes

### Scene

Represents a scene in Stash.

```python
from stash import Scene, VideoCaption

scene = Scene(
    id="123",
    title="Example Scene",
    urls=["https://example.com"],
    rating100=80,
    organized=True,
    captions=[VideoCaption(language_code="en", caption_type="srt")]
)
```

### Performer

Represents a performer in Stash.

```python
from stash import Performer
from stashapi.stash_types import Gender

performer = Performer(
    id="123",
    name="Example Performer",
    gender=Gender.FEMALE,
    urls=["https://example.com"],
    favorite=True
)
```

### Studio

Represents a studio in Stash.

```python
from stash import Studio

studio = Studio(
    id="123",
    name="Example Studio",
    url="https://example.com",
    rating100=90
)
```

### Tag

Represents a tag in Stash.

```python
from stash import Tag

tag = Tag(
    id="123",
    name="example",
    description="An example tag",
    aliases=["ex", "example-tag"]
)
```

### Group

Represents a group (movie/collection) in Stash.

```python
from stash import Group

group = Group(
    id="123",
    name="Example Collection",
    description="An example collection",
    rating100=85
)
```

### Gallery

Represents a gallery in Stash.

```python
from stash import Gallery, GalleryChapter

gallery = Gallery(
    id="123",
    title="Example Gallery",
    photographer="Example Photographer",
    chapters=[
        GalleryChapter(
            id="1",
            title="Chapter 1",
            image_index=0,
            gallery_id="123"
        )
    ]
)
```

### Image

Represents an image in Stash.

```python
from stash import Image, ImagePathsType

image = Image(
    id="123",
    title="Example Image",
    paths=ImagePathsType(
        thumbnail="/thumbs/123.jpg",
        preview="/previews/123.jpg",
        image="/images/123.jpg"
    )
)
```

## File Handling

The library provides classes for handling different types of files:

```python
from stash import BaseFile, ImageFile, SceneFile, VisualFile, FileType

# Create a scene file
scene_file = SceneFile(
    id="123",
    path="/videos",
    basename="video.mp4",
    parent_folder_id="root",
    format="mp4",
    width=1920,
    height=1080,
    duration=300.0
)

# Create an image file
image_file = ImageFile(
    id="456",
    path="/images",
    basename="image.jpg",
    parent_folder_id="root",
    width=1920,
    height=1080
)

# Create a visual file wrapper
visual_file = VisualFile(
    file=scene_file,
    file_type=FileType.VIDEO
)
```

## Protocol Support

The library provides protocol definitions for all types, allowing for interface-based programming:

```python
from stash.types import (
    StashSceneProtocol,
    StashPerformerProtocol,
    StashStudioProtocol,
    StashTagProtocol
)

def process_scene(scene: StashSceneProtocol) -> None:
    """Process a scene regardless of its concrete implementation."""
    print(f"Processing scene: {scene.title}")
```

## Batch Operations

All main classes support batch operations for creating and updating multiple objects:

```python
# Create multiple scenes
scenes = [
    Scene(id="1", title="Scene 1"),
    Scene(id="2", title="Scene 2")
]
Scene.create_batch(interface, scenes)

# Update multiple performers
performers = [
    Performer(id="1", name="Performer 1"),
    Performer(id="2", name="Performer 2")
]
Performer.update_batch(interface, performers)
```

## Error Handling

The library uses proper error handling and type checking:

```python
try:
    scene = Scene.find("non-existent", interface)
    if scene is None:
        print("Scene not found")
except Exception as e:
    print(f"Error: {e}")
```

## Contributing

Contributions are welcome! Please see the contributing guidelines for more details.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
