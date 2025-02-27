"""Stash GraphQL interface for sync operations."""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from ...client import StashClient
from ...types import Gallery, Image, Performer, Scene, Tag

T = TypeVar("T", bound=Any)


class StashInterface:
    """Interface for Stash GraphQL operations.

    This class provides a high-level interface for interacting with Stash's
    GraphQL API, handling:
    - Type-safe operations
    - Error handling
    - Retry logic
    - Resource cleanup

    Example:
        ```python
        interface = StashInterface(client)

        # Find a performer
        performer = await interface.find_performer("123")
        if performer:
            print(f"Found performer: {performer.name}")

        # Create a performer
        data = {
            "name": "Performer Name",
            "url": "https://example.com",
        }
        performer = await interface.create_performer(data)

        # Update a performer
        data = {"name": "New Name"}
        updated = await interface.update_performer("123", data)
        ```
    """

    def __init__(self, client: StashClient) -> None:
        """Initialize interface with Stash client.

        Args:
            client: StashClient instance for GraphQL operations
        """
        self.client = client

    async def find_performer(self, id: str) -> Performer | None:
        """Find a performer by ID.

        Args:
            id: Performer ID to find

        Returns:
            Performer if found, None otherwise

        Raises:
            gql.TransportError: If request fails
            ValueError: If response contains errors
        """
        return await self.client.find_performer(id)

    async def create_performer(self, data: dict[str, Any]) -> Performer:
        """Create a new performer.

        Args:
            data: Performer data dictionary

        Returns:
            Created Performer object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        performer = Performer(**data)
        return await self.client.create_performer(performer)

    async def update_performer(self, id: str, data: dict[str, Any]) -> Performer:
        """Update an existing performer.

        Args:
            id: Performer ID to update
            data: Updated performer data

        Returns:
            Updated Performer object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        # First get existing performer
        performer = await self.find_performer(id)
        if not performer:
            raise ValueError(f"Performer {id} not found")

        # Update fields
        for key, value in data.items():
            setattr(performer, key, value)

        return await self.client.update_performer(performer)

    async def find_scene(self, id: str) -> Scene | None:
        """Find a scene by ID.

        Args:
            id: Scene ID to find

        Returns:
            Scene if found, None otherwise

        Raises:
            gql.TransportError: If request fails
            ValueError: If response contains errors
        """
        return await self.client.find_scene(id)

    async def create_scene(self, data: dict[str, Any]) -> Scene:
        """Create a new scene.

        Args:
            data: Scene data dictionary

        Returns:
            Created Scene object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        scene = Scene(**data)
        return await self.client.create_scene(scene)

    async def update_scene(self, id: str, data: dict[str, Any]) -> Scene:
        """Update an existing scene.

        Args:
            id: Scene ID to update
            data: Updated scene data

        Returns:
            Updated Scene object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        # First get existing scene
        scene = await self.find_scene(id)
        if not scene:
            raise ValueError(f"Scene {id} not found")

        # Update fields
        for key, value in data.items():
            setattr(scene, key, value)

        return await self.client.update_scene(scene)

    async def find_gallery(self, id: str) -> Gallery | None:
        """Find a gallery by ID.

        Args:
            id: Gallery ID to find

        Returns:
            Gallery if found, None otherwise

        Raises:
            gql.TransportError: If request fails
            ValueError: If response contains errors
        """
        return await self.client.find_gallery(id)

    async def create_gallery(self, data: dict[str, Any]) -> Gallery:
        """Create a new gallery.

        Args:
            data: Gallery data dictionary

        Returns:
            Created Gallery object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        gallery = Gallery(**data)
        return await self.client.create_gallery(gallery)

    async def update_gallery(self, id: str, data: dict[str, Any]) -> Gallery:
        """Update an existing gallery.

        Args:
            id: Gallery ID to update
            data: Updated gallery data

        Returns:
            Updated Gallery object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        # First get existing gallery
        gallery = await self.find_gallery(id)
        if not gallery:
            raise ValueError(f"Gallery {id} not found")

        # Update fields
        for key, value in data.items():
            setattr(gallery, key, value)

        return await self.client.update_gallery(gallery)

    async def find_image(self, id: str) -> Image | None:
        """Find an image by ID.

        Args:
            id: Image ID to find

        Returns:
            Image if found, None otherwise

        Raises:
            gql.TransportError: If request fails
            ValueError: If response contains errors
        """
        return await self.client.find_image(id)

    async def create_image(self, data: dict[str, Any]) -> Image:
        """Create a new image.

        Args:
            data: Image data dictionary

        Returns:
            Created Image object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        image = Image(**data)
        return await self.client.create_image(image)

    async def update_image(self, id: str, data: dict[str, Any]) -> Image:
        """Update an existing image.

        Args:
            id: Image ID to update
            data: Updated image data

        Returns:
            Updated Image object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        # First get existing image
        image = await self.find_image(id)
        if not image:
            raise ValueError(f"Image {id} not found")

        # Update fields
        for key, value in data.items():
            setattr(image, key, value)

        return await self.client.update_image(image)

    async def find_tag(self, id: str) -> Tag | None:
        """Find a tag by ID.

        Args:
            id: Tag ID to find

        Returns:
            Tag if found, None otherwise

        Raises:
            gql.TransportError: If request fails
            ValueError: If response contains errors
        """
        return await self.client.find_tag(id)

    async def create_tag(self, data: dict[str, Any]) -> Tag:
        """Create a new tag.

        Args:
            data: Tag data dictionary

        Returns:
            Created Tag object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        tag = Tag(**data)
        return await self.client.create_tag(tag)

    async def update_tag(self, id: str, data: dict[str, Any]) -> Tag:
        """Update an existing tag.

        Args:
            id: Tag ID to update
            data: Updated tag data

        Returns:
            Updated Tag object

        Raises:
            gql.TransportError: If request fails
            ValueError: If data is invalid
        """
        # First get existing tag
        tag = await self.find_tag(id)
        if not tag:
            raise ValueError(f"Tag {id} not found")

        # Update fields
        for key, value in data.items():
            setattr(tag, key, value)

        return await self.client.update_tag(tag)

    async def close(self) -> None:
        """Close the interface and cleanup resources."""
        await self.client.close()

    async def __aenter__(self) -> StashInterface:
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager."""
        await self.close()
