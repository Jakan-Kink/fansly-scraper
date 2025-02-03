# Schema Implementation Details

## Overview
This document tracks our implementation of the GraphQL schema types from `/schema/types/` in our Python Strawberry types.

## Implementation Status

### Core Types (Client-Side)
✅ Implemented:
- config.graphql - Configuration types
- file.graphql - File system types
- filters.graphql - Search and filtering types
- gallery-chapter.graphql - Gallery chapter types
- gallery.graphql - Gallery types
- group.graphql - Group types (replaces Movie)
- image.graphql - Image types
- job.graphql - Job status types
- logging.graphql - Logging types
- metadata.graphql - Metadata types
- performer.graphql - Performer types
- scene-marker-tag.graphql - Scene marker tag types
- scene-marker.graphql - Scene marker types
- scene.graphql - Scene types (in progress)
- studio.graphql - Studio types (pending)
- tag.graphql - Tag types (pending)

### Server-Side (Not Implemented)
These types are server-side only and not needed in our client implementation:
- dlna.graphql - DLNA server configuration
- migration.graphql - Database migration
- package.graphql - Plugin/scraper package management
- plugin.graphql - Plugin management
- scraped-group.graphql - Scraping operations
- scraped-performer.graphql - Scraping operations
- scraper.graphql - Scraping configuration
- sql.graphql - Direct database access
- version.graphql - Server version info

### Not Implementing
These types are either deprecated or not needed for our use case:
- movie.graphql - Deprecated in favor of Group
- stash-box.graphql - Not needed for our client
- stats.graphql - Not needed for our client

## Implementation Patterns

### Type Organization
- Each schema file generally maps to a Python module
- Related types are grouped together (e.g., scene-marker.graphql and scene-marker-tag.graphql in markers.py)
- Common base functionality in base.py (StashObject)

### Common Patterns
1. StashObject Base Class:
   - Not a schema interface but matches common pattern
   - Provides id, created_at, updated_at fields
   - Adds find_by_id and save methods
   - Used by types that have these common fields

2. Field Types:
   - Use Python 3.12 type hints (e.g., str | None instead of Optional[str])
   - Use list[T] instead of List[T]
   - Use | for union types
   - Use strawberry.field for lists with default_factory

3. Documentation:
   - All types reference their schema file
   - Field comments include schema type
   - Resolver fields are marked
   - Deprecated fields are marked with reason

4. Deprecation Handling:
   - Keep deprecated fields for compatibility
   - Add deprecation comments
   - Prefer new fields over deprecated ones
   - Example: Movie -> Group migration

### Field Naming
1. Schema to Python mapping:
   - Use snake_case for Python field names (Strawberry converts to camelCase in GraphQL)
   - PascalCase -> PascalCase for types
   - snake_case for internal helper methods
   - Example:
     ```python
     @strawberry.type
     class Scene:
         play_count: int  # -> playCount in GraphQL
         scene_markers: list[Marker]  # -> sceneMarkers in GraphQL
     ```

2. Special Fields:
   - Resolvers marked with (Resolver)
   - Required fields marked with !
   - Deprecated fields marked with @deprecated

### Type Customization
1. Input Types:
   - Match schema field names exactly
   - Add docstrings with schema reference
   - Include field descriptions

2. Output Types:
   - Add helper methods (e.g., to_input())
   - Add factory methods (e.g., from_media())
   - Keep schema compatibility

## Deviations from Schema

### Intentional Changes
1. StashObject Base Class:
   - Added for code reuse
   - Not in schema but matches common pattern
   - Documented in type docstrings

2. Field Types:
   - Using Python native types
   - Maintaining schema compatibility
   - Example: datetime for Time scalar

### Compatibility Notes
1. Movie Deprecation:
   - Keeping movie-related types in Scene
   - Using groups as primary API
   - Maintaining backward compatibility

2. URL Fields:
   - Keeping deprecated url fields
   - Using urls list as primary API
   - Maintaining backward compatibility

## Testing Strategy
1. Type Tests:
   - Verify schema compatibility
   - Check field types and nullability
   - Test serialization/deserialization

2. Integration Tests:
   - Test with actual API responses
   - Verify field mapping
   - Check deprecated field handling

## Future Considerations
1. Schema Evolution:
   - Monitor for new deprecations
   - Track schema version
   - Plan for updates

2. Type Safety:
   - Consider runtime type checking
   - Add validation where needed
   - Maintain Python 3.12 compatibility
