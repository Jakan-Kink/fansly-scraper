"""Gallery-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindGalleriesResultType, Gallery, GalleryChapter
from ..protocols import StashClientProtocol


class GalleryClientMixin(StashClientProtocol):
    """Mixin for gallery-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_gallery(self, id: str) -> Gallery | None:
        """Find a gallery by its ID.

        Args:
            id: The ID of the gallery to find

        Returns:
            Gallery object if found, None otherwise
        """
        try:
            result = await self.execute(
                fragments.FIND_GALLERY_QUERY,
                {"id": id},
            )
            if result and result.get("findGallery"):
                return Gallery(**result["findGallery"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find gallery {id}: {e}")
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_galleries(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        gallery_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindGalleriesResultType:
        """Find galleries matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            gallery_filter: Optional gallery-specific filter
            q: Optional search query (alternative to filter_["q"])

        Returns:
            FindGalleriesResultType containing:
                - count: Total number of matching galleries
                - galleries: List of Gallery objects
        """
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_GALLERIES_QUERY,
                {"filter": filter_, "gallery_filter": gallery_filter},
            )
            return FindGalleriesResultType(**result["findGalleries"])
        except Exception as e:
            self.log.error(f"Failed to find galleries: {e}")
            return FindGalleriesResultType(count=0, galleries=[])

    async def create_gallery(self, gallery: Gallery) -> Gallery:
        """Create a new gallery in Stash.

        Args:
            gallery: Gallery object with the data to create. Required fields:
                - title: Gallery title

        Returns:
            Created Gallery object with ID and any server-generated fields

        Raises:
            ValueError: If the gallery data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await gallery.to_input()
            result = await self.execute(
                fragments.CREATE_GALLERY_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified galleries
            self.find_gallery.cache_clear()
            self.find_galleries.cache_clear()
            return Gallery(**result["galleryCreate"])
        except Exception as e:
            self.log.error(f"Failed to create gallery: {e}")
            raise

    async def update_gallery(self, gallery: Gallery) -> Gallery:
        """Update an existing gallery in Stash.

        Args:
            gallery: Gallery object with updated data. Required fields:
                - id: Gallery ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Gallery object with any server-generated fields

        Raises:
            ValueError: If the gallery data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await gallery.to_input()
            result = await self.execute(
                fragments.UPDATE_GALLERY_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified a gallery
            self.find_gallery.cache_clear()
            self.find_galleries.cache_clear()
            return Gallery(**result["galleryUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update gallery: {e}")
            raise

    async def galleries_update(self, galleries: list[Gallery]) -> list[Gallery]:
        """Update multiple galleries with individual data.

        Args:
            galleries: List of Gallery objects to update, each must have an ID

        Returns:
            List of updated Gallery objects
        """
        try:
            result = await self.execute(
                fragments.GALLERIES_UPDATE_MUTATION,
                {"input": [await gallery.to_input() for gallery in galleries]},
            )
            # Clear caches since we've modified galleries
            self.find_gallery.cache_clear()
            self.find_galleries.cache_clear()
            return [Gallery(**gallery) for gallery in result["galleriesUpdate"]]
        except Exception as e:
            self.log.error(f"Failed to update galleries: {e}")
            raise

    async def gallery_destroy(
        self,
        ids: list[str],
        delete_file: bool | None = None,
        delete_generated: bool | None = None,
    ) -> bool:
        """Delete galleries.

        Args:
            ids: List of gallery IDs to delete
            delete_file: If true, delete associated files
            delete_generated: If true, delete generated files

        Returns:
            True if successful
        """
        try:
            result = await self.execute(
                fragments.GALLERY_DESTROY_MUTATION,
                {
                    "input": {
                        "ids": ids,
                        "delete_file": delete_file,
                        "delete_generated": delete_generated,
                    }
                },
            )
            # Clear caches since we've deleted galleries
            self.find_gallery.cache_clear()
            self.find_galleries.cache_clear()
            return result["galleryDestroy"]
        except Exception as e:
            self.log.error(f"Failed to destroy galleries {ids}: {e}")
            raise

    async def remove_gallery_images(
        self,
        gallery_id: str,
        image_ids: list[str],
    ) -> bool:
        """Remove images from a gallery.

        Args:
            gallery_id: Gallery ID
            image_ids: List of image IDs to remove

        Returns:
            True if successful
        """
        try:
            result = await self.execute(
                fragments.REMOVE_GALLERY_IMAGES_MUTATION,
                {"input": {"gallery_id": gallery_id, "image_ids": image_ids}},
            )
            # Clear cache for this gallery since we've modified its images
            self.find_gallery.cache_clear()
            return result["removeGalleryImages"]
        except Exception as e:
            self.log.error(f"Failed to remove images from gallery {gallery_id}: {e}")
            raise

    async def set_gallery_cover(
        self,
        gallery_id: str,
        cover_image_id: str,
    ) -> bool:
        """Set the cover image for a gallery.

        Args:
            gallery_id: Gallery ID
            cover_image_id: ID of the image to use as cover

        Returns:
            True if successful
        """
        try:
            result = await self.execute(
                fragments.SET_GALLERY_COVER_MUTATION,
                {"input": {"gallery_id": gallery_id, "cover_image_id": cover_image_id}},
            )
            # Clear cache for this gallery since we've modified its cover
            self.find_gallery.cache_clear()
            return result["setGalleryCover"]
        except Exception as e:
            self.log.error(f"Failed to set cover for gallery {gallery_id}: {e}")
            raise

    async def reset_gallery_cover(self, gallery_id: str) -> bool:
        """Reset the cover image for a gallery.

        Args:
            gallery_id: Gallery ID

        Returns:
            True if successful
        """
        try:
            result = await self.execute(
                fragments.RESET_GALLERY_COVER_MUTATION,
                {"input": {"gallery_id": gallery_id}},
            )
            # Clear cache for this gallery since we've modified its cover
            self.find_gallery.cache_clear()
            return result["resetGalleryCover"]
        except Exception as e:
            self.log.error(f"Failed to reset cover for gallery {gallery_id}: {e}")
            raise

    async def gallery_chapter_create(
        self,
        gallery_id: str,
        title: str,
        image_index: int,
    ) -> GalleryChapter:
        """Create a new gallery chapter.

        Args:
            gallery_id: Gallery ID
            title: Chapter title
            image_index: Index of the image where the chapter starts

        Returns:
            Created GalleryChapter object
        """
        try:
            result = await self.execute(
                fragments.GALLERY_CHAPTER_CREATE_MUTATION,
                {
                    "input": {
                        "gallery_id": gallery_id,
                        "title": title,
                        "image_index": image_index,
                    }
                },
            )
            # Clear cache for this gallery since we've modified its chapters
            self.find_gallery.cache_clear()
            return GalleryChapter(**result["galleryChapterCreate"])
        except Exception as e:
            self.log.error(f"Failed to create chapter for gallery {gallery_id}: {e}")
            raise

    async def gallery_chapter_update(
        self,
        id: str,
        gallery_id: str | None = None,
        title: str | None = None,
        image_index: int | None = None,
    ) -> GalleryChapter:
        """Update a gallery chapter.

        Args:
            id: Chapter ID
            gallery_id: Optional gallery ID to move chapter to
            title: Optional new title
            image_index: Optional new image index

        Returns:
            Updated GalleryChapter object
        """
        try:
            input_data = {"id": id}
            if gallery_id is not None:
                input_data["gallery_id"] = gallery_id
            if title is not None:
                input_data["title"] = title
            if image_index is not None:
                input_data["image_index"] = image_index

            result = await self.execute(
                fragments.GALLERY_CHAPTER_UPDATE_MUTATION,
                {"input": input_data},
            )
            # Clear cache for affected galleries since we've modified chapters
            self.find_gallery.cache_clear()
            return GalleryChapter(**result["galleryChapterUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update chapter {id}: {e}")
            raise

    async def add_gallery_images(
        self,
        gallery_id: str,
        image_ids: list[str],
    ) -> bool:
        """Add images to a gallery.

        Args:
            gallery_id: Gallery ID
            image_ids: List of image IDs to add

        Returns:
            True if successful

        Examples:
            Add images to a gallery:
            ```python
            success = await client.add_gallery_images(
                gallery_id=gallery.id,
                image_ids=["456", "789"],
            )
            ```
        """
        try:
            result = await self.execute(
                fragments.GALLERY_ADD_IMAGES_MUTATION,
                {"input": {"gallery_id": gallery_id, "image_ids": image_ids}},
            )
            # Clear cache for this gallery since we've modified its images
            self.find_gallery.cache_clear()
            return result["addGalleryImages"]
        except Exception as e:
            self.log.error(f"Failed to add images to gallery {gallery_id}: {e}")
            raise

    async def gallery_chapter_destroy(self, id: str) -> bool:
        """Delete a gallery chapter.

        Args:
            id: Chapter ID

        Returns:
            True if successful
        """
        try:
            result = await self.execute(
                fragments.GALLERY_CHAPTER_DESTROY_MUTATION,
                {"id": id},
            )
            # Clear caches since we've modified galleries by removing a chapter
            self.find_gallery.cache_clear()
            return result["galleryChapterDestroy"]
        except Exception as e:
            self.log.error(f"Failed to destroy chapter {id}: {e}")
            raise
