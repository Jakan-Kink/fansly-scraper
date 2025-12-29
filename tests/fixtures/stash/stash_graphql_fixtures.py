"""Fixtures for Stash GraphQL responses using respx for edge mocking.

This module provides helper functions and fixtures for creating GraphQL response
data that matches the stash/types/*ResultType format. Use these with respx to mock
HTTP responses at the edge.

Key Principles:
- NO MagicMock for internal functions
- Use respx to mock GraphQL HTTP POST requests
- Provide real JSON responses matching Stash GraphQL schema
- Return data in the format that gql.Client.execute() returns

Usage:
    import respx
    import httpx
    from tests.fixtures.stash import create_graphql_response, create_find_tags_result

    @respx.mock
    async def test_find_tags(tag_mixin):
        # Create response data
        tags_data = create_find_tags_result(count=1, tags=[
            {"id": "123", "name": "test", "aliases": [], "parents": [], "children": []}
        ])

        # Mock the GraphQL HTTP endpoint
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findTags", tags_data)
            )
        )

        # Initialize client and test
        await tag_mixin.context.get_client()
        result = await tag_mixin.context.client.find_tags(tag_filter={"name": {"value": "test"}})
        assert result.count == 1
"""

from typing import Any


def create_graphql_response(
    operation: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a GraphQL response envelope.

    GraphQL responses have the format: {"data": {operationName: resultData}}

    Args:
        operation: The GraphQL operation name (e.g., "findTags", "tagCreate")
        data: The operation result data

    Returns:
        Complete GraphQL response dict

    Example:
        response = create_graphql_response("findTags", {
            "count": 1,
            "tags": [{"id": "123", "name": "test"}]
        })
        # Returns: {"data": {"findTags": {"count": 1, "tags": [...]}}}
    """
    if data is None:
        data = {}

    return {"data": {operation: data}}


def create_find_tags_result(
    count: int = 0, tags: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Create a findTags query result matching FindTagsResultType.

    Args:
        count: Total number of tags
        tags: List of tag dicts with fields: id, name, aliases, parents, children, description, image_path

    Returns:
        Dict matching FindTagsResultType schema

    Example:
        result = create_find_tags_result(count=2, tags=[
            {"id": "1", "name": "tag1", "aliases": [], "parents": [], "children": []},
            {"id": "2", "name": "tag2", "aliases": ["alias"], "parents": [], "children": []},
        ])
    """
    if tags is None:
        tags = []

    return {"count": count, "tags": tags}


def create_tag_dict(
    id: str,
    name: str,
    aliases: list[str] | None = None,
    parents: list[dict] | None = None,
    children: list[dict] | None = None,
    description: str | None = None,
    image_path: str | None = None,
) -> dict[str, Any]:
    """Create a Tag dict matching the Tag type schema.

    Args:
        id: Tag ID
        name: Tag name
        aliases: List of alias strings
        parents: List of parent tag dicts (recursive)
        children: List of child tag dicts (recursive)
        description: Tag description
        image_path: Path to tag image

    Returns:
        Dict matching Tag type

    Example:
        tag = create_tag_dict(
            id="123",
            name="test_tag",
            aliases=["alias1", "alias2"],
            description="A test tag"
        )
    """
    return {
        "id": id,
        "name": name,
        "aliases": aliases or [],
        "parents": parents or [],
        "children": children or [],
        "description": description,
        "image_path": image_path,
    }


def create_tag_create_result(tag: dict[str, Any]) -> dict[str, Any]:
    """Create a tagCreate mutation result.

    Args:
        tag: Tag dict created with create_tag_dict()

    Returns:
        Dict matching the tagCreate mutation result

    Example:
        tag = create_tag_dict(id="123", name="new_tag")
        result = create_tag_create_result(tag)
        # Use with: create_graphql_response("tagCreate", result)
    """
    return tag


def create_find_performers_result(
    count: int = 0, performers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Create a findPerformers query result matching FindPerformersResultType.

    Args:
        count: Total number of performers
        performers: List of performer dicts

    Returns:
        Dict matching FindPerformersResultType schema
    """
    if performers is None:
        performers = []

    return {"count": count, "performers": performers}


def create_performer_dict(
    id: str,
    name: str,
    urls: list[str] | None = None,
    gender: str | None = None,
    tags: list[dict] | None = None,
    stash_ids: list[dict] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create a Performer dict matching the Performer type schema.

    Args:
        id: Performer ID
        name: Performer name
        urls: List of URL strings
        gender: Gender enum value
        tags: List of tag dicts
        stash_ids: List of StashID dicts
        **kwargs: Additional performer fields

    Returns:
        Dict matching Performer type
    """
    base = {
        "id": id,
        "name": name,
        "alias_list": [],
        "urls": urls or [],
        "gender": gender,
        "tags": tags or [],
        "stash_ids": stash_ids or [],
        "scenes": [],
        "groups": [],
    }
    base.update(kwargs)
    return base


def create_find_studios_result(
    count: int = 0, studios: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Create a findStudios query result matching FindStudiosResultType.

    Args:
        count: Total number of studios
        studios: List of studio dicts

    Returns:
        Dict matching FindStudiosResultType schema
    """
    if studios is None:
        studios = []

    return {"count": count, "studios": studios}


def create_studio_dict(
    id: str,
    name: str,
    urls: list[str] | None = None,
    parent_studio: dict | None = None,
    aliases: list[str] | None = None,
    tags: list[dict] | None = None,
    stash_ids: list[dict] | None = None,
    details: str | None = None,
) -> dict[str, Any]:
    """Create a Studio dict matching the Studio type schema.

    Args:
        id: Studio ID
        name: Studio name
        urls: List of Studio URLs (Stash schema change from url to urls)
        parent_studio: Parent studio dict (recursive)
        aliases: List of alias strings
        tags: List of tag dicts
        stash_ids: List of StashID dicts
        details: Studio details

    Returns:
        Dict matching Studio type
    """
    return {
        "id": id,
        "name": name,
        "urls": urls or [],
        "parent_studio": parent_studio,
        "aliases": aliases or [],
        "tags": tags or [],
        "stash_ids": stash_ids or [],
        "details": details,
    }


def create_find_scenes_result(
    count: int = 0,
    scenes: list[dict[str, Any]] | None = None,
    duration: float = 0.0,
    filesize: float = 0.0,
) -> dict[str, Any]:
    """Create a findScenes query result matching FindScenesResultType.

    Args:
        count: Total number of scenes
        scenes: List of scene dicts
        duration: Total duration of all scenes in seconds
        filesize: Total file size of all scenes in bytes

    Returns:
        Dict matching FindScenesResultType schema
    """
    if scenes is None:
        scenes = []

    return {
        "count": count,
        "scenes": scenes,
        "duration": duration,
        "filesize": filesize,
    }


def create_scene_dict(
    id: str,
    title: str,
    studio: dict | None = None,
    performers: list[dict] | None = None,
    tags: list[dict] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create a Scene dict matching the Scene type schema.

    Args:
        id: Scene ID
        title: Scene title
        studio: Studio dict
        performers: List of performer dicts
        tags: List of tag dicts
        **kwargs: Additional scene fields

    Returns:
        Dict matching Scene type
    """
    base = {
        "id": id,
        "title": title,
        "studio": studio,
        "performers": performers or [],
        "tags": tags or [],
        "files": [],
        "urls": [],
        "stash_ids": [],
        "groups": [],
        "galleries": [],
        "scene_markers": [],
        "sceneStreams": [],
        "captions": [],
        "organized": False,
    }
    base.update(kwargs)
    return base


def create_find_images_result(
    count: int = 0,
    images: list[dict[str, Any]] | None = None,
    megapixels: float = 0.0,
    filesize: float = 0.0,
) -> dict[str, Any]:
    """Create a findImages query result matching FindImagesResultType.

    Args:
        count: Total number of images
        images: List of image dicts
        megapixels: Total megapixels of all images
        filesize: Total file size of all images in bytes

    Returns:
        Dict matching FindImagesResultType schema
    """
    if images is None:
        images = []

    return {
        "count": count,
        "images": images,
        "megapixels": megapixels,
        "filesize": filesize,
    }


def create_image_dict(
    id: str,
    title: str | None = None,
    studio: dict | None = None,
    performers: list[dict] | None = None,
    tags: list[dict] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create an Image dict matching the Image type schema.

    Args:
        id: Image ID
        title: Image title
        studio: Studio dict
        performers: List of performer dicts
        tags: List of tag dicts
        **kwargs: Additional image fields

    Returns:
        Dict matching Image type
    """
    base = {
        "id": id,
        "title": title,
        "studio": studio,
        "performers": performers or [],
        "tags": tags or [],
        "visual_files": [],
        "urls": [],
        "galleries": [],
        "organized": False,
    }
    base.update(kwargs)
    return base


def create_find_galleries_result(
    count: int = 0, galleries: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Create a findGalleries query result matching FindGalleriesResultType.

    Args:
        count: Total number of galleries
        galleries: List of gallery dicts

    Returns:
        Dict matching FindGalleriesResultType schema
    """
    if galleries is None:
        galleries = []

    return {"count": count, "galleries": galleries}


def create_gallery_dict(
    id: str,
    title: str | None = None,
    code: str | None = None,
    urls: list[str] | None = None,
    studio: dict | None = None,
    performers: list[dict] | None = None,
    tags: list[dict] | None = None,
    scenes: list[dict] | None = None,
    images: list[dict] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create a Gallery dict matching the Gallery type schema.

    Args:
        id: Gallery ID
        title: Gallery title
        code: Gallery code
        urls: List of URL strings
        studio: Studio dict
        performers: List of performer dicts
        tags: List of tag dicts
        scenes: List of scene dicts
        images: List of image dicts
        **kwargs: Additional gallery fields

    Returns:
        Dict matching Gallery type
    """
    base = {
        "id": id,
        "title": title,
        "code": code,
        "urls": urls or [],
        "studio": studio,
        "performers": performers or [],
        "tags": tags or [],
        "scenes": scenes or [],
        "images": images or [],
        "files": [],
        "organized": False,
    }
    base.update(kwargs)
    return base


__all__ = [
    "create_find_galleries_result",
    "create_find_images_result",
    "create_find_performers_result",
    "create_find_scenes_result",
    "create_find_studios_result",
    "create_find_tags_result",
    "create_gallery_dict",
    "create_graphql_response",
    "create_image_dict",
    "create_performer_dict",
    "create_scene_dict",
    "create_studio_dict",
    "create_tag_create_result",
    "create_tag_dict",
]
