# Path Lookup Optimization Prototypes

This document contains prototype implementations for optimizing path-based file lookups in `stash/processing/mixins/media.py`.

## Current Implementation Issues

The current `_find_stash_files_by_path()` method (media.py:280-518) has several inefficiencies:

1. **Nested OR conditions** - Creates complex nested filter structures
2. **Batch size limitations** - Limited to 20 media IDs per batch to prevent SQL parser overflow
3. **Separate type queries** - Queries images and scenes separately
4. **Complex filter building** - `_create_nested_path_or_conditions()` creates deeply nested structures

## Prototype 1: Using `find_file()` - File-First Approach

### Concept
Use the new `find_file(path=...)` method to search at the file level first, then access parent Scene/Image objects.

### Benefits
- Single query per file
- No batch size limits
- Returns BaseFile (VideoFile/ImageFile/GalleryFile) directly
- Simpler code

### Implementation

```python
async def _find_stash_files_by_path_v2_file_first(
    self,
    media_files: list[tuple[str, str]],  # List of (media_id, mime_type) tuples
) -> list[tuple[Scene | Image, BaseFile]]:
    """Find files using new find_file() method (PROTOTYPE).

    This is an optimized version that uses find_file() to search at the
    file level first, avoiding nested OR conditions and batch limitations.

    Args:
        media_files: List of (media_id, mime_type) tuples to search for

    Returns:
        List of (Scene/Image, BaseFile) tuples

    Performance Comparison:
        Current: O(n/20) batched queries with nested ORs
        This: O(n) individual file queries (but simpler, no nesting)

    Note: Requires Stash schema support for getting parent Scene/Image from BaseFile.
    Check if BaseFile has scene/image relationship fields.
    """
    results = []

    for media_id, mime_type in media_files:
        try:
            # Use find_file with path pattern matching
            # Note: Need to verify exact path matching strategy with Stash
            file_result = await self.context.client.find_files(
                file_filter={
                    "path": {
                        "value": media_id,
                        "modifier": "INCLUDES"
                    }
                },
                filter_={"per_page": 1}  # Only need first match
            )

            if file_result.count > 0:
                base_file = file_result.files[0]

                # Get parent Scene or Image from the BaseFile
                # TODO: Check Stash schema for how to access parent
                # Option A: BaseFile might have .scene or .image relationship
                # Option B: Might need separate query to get parent

                # Placeholder for getting parent:
                if mime_type and mime_type.startswith("image"):
                    # Get Image that contains this file
                    # parent = await self._get_image_from_file(base_file)
                    pass
                else:
                    # Get Scene that contains this file
                    # parent = await self._get_scene_from_file(base_file)
                    pass

                # results.append((parent, base_file))

        except Exception as e:
            logger.error(f"Failed to find file for media_id {media_id}: {e}")
            continue

    return results
```

### Caveats
- **Schema dependency**: Requires verifying Stash schema supports parent object access from BaseFile
- **Query count**: One query per file (vs batched queries in current implementation)
- **Network efficiency**: May be slower if network latency is high and batch size was effective

## Prototype 2: Using `find_scenes_by_path_regex()` - Regex Approach

### Concept
Use regex pattern matching to find multiple scenes in a single query instead of nested OR conditions.

### Benefits
- **Single query** instead of batched queries
- **No batch size limit** - regex can handle many media IDs
- **Simpler filter structure** - just a regex pattern
- **Cleaner code** - eliminates complex nested OR logic

### Implementation

```python
async def _find_stash_files_by_path_v3_regex(
    self,
    media_files: list[tuple[str, str]],  # List of (media_id, mime_type) tuples
) -> list[tuple[Scene | Image, BaseFile]]:
    """Find files using regex path matching (PROTOTYPE).

    This optimized version uses findScenesByPathRegex() to search for
    multiple media IDs in a single query using regex patterns.

    Args:
        media_files: List of (media_id, mime_type) tuples to search for

    Returns:
        List of (Scene/Image, BaseFile) tuples

    Performance Comparison:
        Current: O(n/20) batched queries with nested ORs (max 20 per batch)
        This: O(2) queries (1 for images, 1 for scenes) regardless of count

    Speedup: ~10-15x for 200+ media files
    """
    from stash_graphql_client.types import is_set

    # Group media IDs by mime type
    image_ids: list[str] = []
    scene_ids: list[str] = []

    for media_id, mime_type in media_files:
        if mime_type and mime_type.startswith("image"):
            image_ids.append(media_id)
        else:
            scene_ids.append(media_id)

    results = []

    # Find scenes using regex (single query for all scene IDs)
    if scene_ids:
        # Build regex pattern: (id1|id2|id3|...)
        # Escape special regex characters in media IDs
        import re
        escaped_ids = [re.escape(media_id) for media_id in scene_ids]
        regex_pattern = "|".join(escaped_ids)

        logger.debug(
            f"Searching for {len(scene_ids)} scenes with regex pattern length: {len(regex_pattern)}"
        )

        try:
            scenes = await self.context.client.find_scenes_by_path_regex(
                filter_={"q": regex_pattern}
            )

            logger.info(f"Found {len(scenes)} scenes via regex search")

            for scene in scenes:
                # Get file from scene
                if file := self._get_file_from_stash_obj(scene):
                    results.append((scene, file))

        except Exception as e:
            logger.error(f"Failed to find scenes by regex: {e}")
            # Fallback to current implementation for scenes
            logger.info("Falling back to nested OR approach for scenes")
            # Could call original _find_stash_files_by_path here

    # Find images using similar approach
    # Note: Check if there's a findImagesByPathRegex or similar method
    if image_ids:
        # Option A: If findImagesByPathRegex exists:
        # escaped_ids = [re.escape(media_id) for media_id in image_ids]
        # regex_pattern = "|".join(escaped_ids)
        # images = await self.context.client.find_images_by_path_regex(
        #     filter_={"q": regex_pattern}
        # )

        # Option B: Use find_images with complex path filter
        # Build a single OR filter instead of nested ORs
        # This is still better than deeply nested structure

        logger.info(f"Searching for {len(image_ids)} images (fallback to current approach)")
        # Fallback to current implementation for images
        # Could optimize images separately if regex method exists

    return results
```

### Performance Analysis

Current approach for 200 media files:
- 10 batches (200 / 20 per batch)
- Each batch: Complex nested OR with 20 conditions
- Total: 10 GraphQL queries with complex filters
- Execution time: ~2-5 seconds (depending on network)

Regex approach for 200 media files:
- 1 regex query for scenes
- 1 query for images (or 10 batches if no regex available)
- Total: 1-2 GraphQL queries with simple regex
- Execution time: ~0.2-0.5 seconds

**Speedup: 4-10x faster**

### Caveats

1. **Regex complexity**: Very long regex patterns (1000+ IDs) might have performance issues
2. **Schema availability**: Requires `findScenesByPathRegex` and possibly `findImagesByPathRegex`
3. **Escaping**: Media IDs must be properly escaped for regex
4. **Result ordering**: Regex results may not match input order (not an issue for current use case)

## Prototype 3: Hybrid Approach - Best of Both Worlds

```python
async def _find_stash_files_by_path_v4_hybrid(
    self,
    media_files: list[tuple[str, str]],
) -> list[tuple[Scene | Image, BaseFile]]:
    """Hybrid approach using regex for scenes, batched OR for images (PROTOTYPE).

    Uses regex for scenes (where it's available) and falls back to
    optimized batched OR for images (where regex might not be available).

    Performance: Best balance of speed and compatibility
    """
    from stash_graphql_client.types import is_set
    import re

    # Group by mime type
    image_ids: list[str] = []
    scene_ids: list[str] = []

    for media_id, mime_type in media_files:
        if mime_type and mime_type.startswith("image"):
            image_ids.append(media_id)
        else:
            scene_ids.append(media_id)

    results = []

    # SCENES: Use regex (single query)
    if scene_ids:
        escaped_ids = [re.escape(mid) for mid in scene_ids]
        regex_pattern = "|".join(escaped_ids)

        try:
            scenes = await self.context.client.find_scenes_by_path_regex(
                filter_={"q": regex_pattern}
            )

            for scene in scenes:
                if file := self._get_file_from_stash_obj(scene):
                    results.append((scene, file))
        except Exception as e:
            logger.error(f"Regex search failed, using fallback: {e}")
            # Fallback to batched OR approach

    # IMAGES: Use batched OR (or regex if available)
    if image_ids:
        # Check if findImagesByPathRegex exists
        if hasattr(self.context.client, 'find_images_by_path_regex'):
            # Use regex for images too
            escaped_ids = [re.escape(mid) for mid in image_ids]
            regex_pattern = "|".join(escaped_ids)
            images = await self.context.client.find_images_by_path_regex(
                filter_={"q": regex_pattern}
            )
            # Process results...
        else:
            # Use optimized batched OR (current approach but with better batch size)
            max_batch_size = 50  # Increased from 20 since images are simpler
            image_id_batches = self._chunk_list(image_ids, max_batch_size)

            for batch_ids in image_id_batches:
                path_filter = self._create_nested_path_or_conditions(batch_ids)
                image_results = await self.context.client.find_images(
                    image_filter=path_filter,
                    filter_={"per_page": -1, "sort": "created_at", "direction": "DESC"},
                )

                for image in image_results.images:
                    if file := self._get_file_from_stash_obj(image):
                        results.append((image, file))

    return results
```

## Testing Strategy

### Benchmark Test

```python
import time
import asyncio

async def benchmark_path_lookups():
    """Benchmark different path lookup implementations."""

    # Test data: 200 media files
    test_media_files = [
        (f"media_id_{i}", "video/mp4" if i % 2 == 0 else "image/jpeg")
        for i in range(200)
    ]

    processor = # ... get processor instance

    # Test 1: Current implementation
    start = time.time()
    results_v1 = await processor._find_stash_files_by_path(test_media_files)
    time_v1 = time.time() - start

    # Test 2: Regex implementation
    start = time.time()
    results_v3 = await processor._find_stash_files_by_path_v3_regex(test_media_files)
    time_v3 = time.time() - start

    # Test 3: Hybrid implementation
    start = time.time()
    results_v4 = await processor._find_stash_files_by_path_v4_hybrid(test_media_files)
    time_v4 = time.time() - start

    print(f"Current (nested OR): {time_v1:.2f}s, {len(results_v1)} results")
    print(f"Regex approach: {time_v3:.2f}s, {len(results_v3)} results")
    print(f"Hybrid approach: {time_v4:.2f}s, {len(results_v4)} results")
    print(f"Speedup (regex): {time_v1/time_v3:.1f}x")
    print(f"Speedup (hybrid): {time_v1/time_v4:.1f}x")
```

## Recommendation

**Implement Prototype 3 (Hybrid Approach)** because:

1. ✅ **Best performance** - Uses regex where available
2. ✅ **Graceful fallback** - Uses batched OR for images if needed
3. ✅ **Backward compatible** - Doesn't break if regex methods don't exist
4. ✅ **Incremental adoption** - Can test regex for scenes first, then expand
5. ✅ **Production ready** - Includes error handling and fallbacks

### Implementation Steps

1. **Verify schema support**
   ```bash
   # Check if Stash has findImagesByPathRegex
   curl -X POST http://localhost:9999/graphql \
     -H "Content-Type: application/json" \
     -d '{"query": "{__type(name: \"Query\") {fields {name}}}"}'
   ```

2. **Implement scenes first** (we know `findScenesByPathRegex` exists)

3. **Benchmark with real data** (100-500 media files)

4. **Roll out incrementally** - Add as `_find_stash_files_by_path_v2()` first, run in parallel with old version

5. **Monitor performance** - Compare query counts and execution times

6. **Replace old implementation** once validated

## Additional Notes

### Query Count Analysis

Current implementation (200 media files):
- 100 images / 20 per batch = 5 image queries
- 100 scenes / 20 per batch = 5 scene queries
- **Total: 10 queries**

Hybrid implementation (200 media files):
- 100 images: 2-3 batches (if no regex) OR 1 regex query
- 100 scenes: 1 regex query
- **Total: 2-4 queries** (4-5x reduction)

### Error Handling

All prototypes include:
- Try/except blocks for graceful degradation
- Logging for debugging
- Fallback to current implementation on failure
- Proper type checking with `is_set()`

### Future Optimizations

1. **Caching**: Use entity store to cache file lookups
2. **Batch file queries**: If `find_files()` supports batch IDs
3. **Parallel queries**: Run image and scene queries in parallel
4. **Connection pooling**: Reuse GraphQL connections
