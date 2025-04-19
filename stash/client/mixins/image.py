"""Image-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindImagesResultType, Image
from ..protocols import StashClientProtocol
from ..utils import sanitize_model_data


class ImageClientMixin(StashClientProtocol):
    """Mixin for image-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_image(self, id: str) -> Image | None:
        """Find an image by its ID.

        Args:
            id: The ID of the image to find

        Returns:
            Image object if found, None otherwise
        """
        try:
            result = await self.execute(
                fragments.FIND_IMAGE_QUERY,
                {"id": id},
            )
            if result and result.get("findImage"):
                # Sanitize model data before creating Image
                clean_data = sanitize_model_data(result["findImage"])
                return Image(**clean_data)
            return None
        except Exception as e:
            self.log.error(f"Failed to find image {id}: {e}")
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_images(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        image_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindImagesResultType:
        """Find images matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            image_filter: Optional image-specific filter
            q: Optional search query (alternative to filter_["q"])

        Returns:
            FindImagesResultType containing:
                - count: Total number of matching images
                - images: List of Image objects
        """
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_IMAGES_QUERY,
                {"filter": filter_, "image_filter": image_filter},
            )
            return FindImagesResultType(**result["findImages"])
        except Exception as e:
            self.log.error(f"Failed to find images: {e}")
            return FindImagesResultType(count=0, images=[])

    async def create_image(self, image: Image) -> Image:
        """Create a new image in Stash.

        Args:
            image: Image object with the data to create. Required fields:
                - title: Image title

        Returns:
            Created Image object with ID and any server-generated fields

        Raises:
            ValueError: If the image data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await image.to_input()
            result = await self.execute(
                fragments.CREATE_IMAGE_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified images
            self.find_image.cache_clear()
            self.find_images.cache_clear()
            # Sanitize model data before creating Image
            clean_data = sanitize_model_data(result["imageCreate"])
            return Image(**clean_data)
        except Exception as e:
            self.log.error(f"Failed to create image: {e}")
            raise

    async def update_image(self, image: Image) -> Image:
        """Update an existing image in Stash.

        Args:
            image: Image object with updated data. Required fields:
                - id: Image ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Image object with any server-generated fields

        Raises:
            ValueError: If the image data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await image.to_input()
            result = await self.execute(
                fragments.UPDATE_IMAGE_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified an image
            self.find_image.cache_clear()
            self.find_images.cache_clear()
            # Sanitize model data before creating Image
            clean_data = sanitize_model_data(result["imageUpdate"])
            return Image(**clean_data)
        except Exception as e:
            self.log.error(f"Failed to update image: {e}")
            raise
