from datetime import datetime

from stashapi.stashapp import StashInterface

from .types import StashGalleryProtocol

gallery_fragment = (
    "id "
    "title "
    "code "
    "urls "
    "date "
    "details "
    "created_at "
    "updated_at "
    "files { "
    "id "
    "path "
    "basename "
    "parent_folder_id "
    "size "
    "zip_file_id "
    "created_at "
    "updated_at "
    "} "
    "scenes { "
    "id "
    "} "
    "performers { "
    "id "
    "} "
    "tags { "
    "id "
    "} "
    "cover { id } "
    "studio { "
    "id "
    "} "
)


class StashGallery(StashGalleryProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashGallery":
        """Find a gallery by ID.

        Args:
            id: The ID of the gallery to find
            interface: StashInterface instance to use for querying

        Returns:
            StashGallery instance if found, None otherwise
        """
        data = interface.find_gallery(id)
        return StashGallery.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashGallery"]:
        """Find all galleries matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashGallery instances matching the criteria
        """
        data = interface.find_galleries(filter=filter, q=q)
        return [StashGallery.from_dict(g) for g in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this gallery in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_gallery(self.to_dict())

    @staticmethod
    def update_batch(
        interface: StashInterface, galleries: list["StashGallery"]
    ) -> list[dict]:
        """Update multiple galleries at once.

        Args:
            interface: StashInterface instance to use for updating
            galleries: List of StashGallery instances to update

        Returns:
            List of updated gallery data from stash
        """
        updates = [g.to_update_input_dict() for g in galleries]
        return interface.update_galleries(updates)

    def to_dict(self) -> dict:
        gallery_dict = {
            "id": self.id,
            "urls": self.urls,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "title": self.title,
            "code": self.code,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "rating100": self.rating100,
            "organized": self.organized,
            "files": [file.get_path() for file in self.files],
            "folder": self.folder,
            "chapters": self.chapters,
            "scenes": [scene.id for scene in self.scenes],
            "studio": self.studio.id if self.studio else None,
            "image_count": self.image_count,
            "tags": self.tags,
            "performers": [performer.id for performer in self.performers],
            "cover": self.cover,
            "paths": self.paths,
        }
        return gallery_dict

    def __init__(
        self,
        id: str,
        urls: list[str] = [],
        title: str | None = None,
        code: str | None = None,
        date: datetime | str | None = None,
        details: str | None = None,
        photographer: str | None = None,
        rating100: int | None = None,
        organized: bool = False,
        created_at: datetime | str | None = None,
        updated_at: datetime | str | None = None,
    ) -> None:
        StashGalleryProtocol.__init__(
            self=self, id=id, urls=urls, created_at=created_at, updated_at=updated_at
        )
        self.title = title
        self.code = code
        self.date = self.sanitize_datetime(date)
        self.details = details
        self.photographer = photographer
        self.rating100 = rating100
        self.organized = organized
        self.files = []
        self.folder = None
        self.chapters = []
        self.scenes = []
        self.studio = None
        self.image_count = 0
        self.tags = []
        self.performers = []
        self.cover = None
        self.paths = None

    def image(self, index: int):
        # Implement logic to retrieve image by index
        pass

    def to_create_input_dict(self) -> dict:
        """Converts the StashGallery object into a dictionary matching the GalleryCreateInput GraphQL definition."""
        return {
            "title": self.title,
            "code": self.code,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "rating100": self.rating100,
            "organized": self.organized,
            "scene_ids": [scene.id for scene in self.scenes],
            "studio_id": self.studio.id if self.studio else None,
            "tag_ids": [tag.id for tag in self.tags],
            "performer_ids": [performer.id for performer in self.performers],
            "cover_image": self.cover.id if self.cover else None,
        }

    def to_update_input_dict(self) -> dict:
        """Converts the StashGallery object into a dictionary matching the GalleryUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the gallery in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created gallery data
        """
        return interface.create_gallery(self.to_create_input_dict())

    def find_images(self, interface: StashInterface) -> list[dict]:
        """Find all images associated with this gallery.

        Args:
            interface: StashInterface instance to use for querying

        Returns:
            list[dict]: List of image data from stash
        """
        return interface.find_gallery_images(self.id)

    def add_images(self, interface: StashInterface, image_ids: list[str]) -> bool:
        """Add images to this gallery.

        Args:
            interface: StashInterface instance to use for adding images
            image_ids: List of image IDs to add to the gallery

        Returns:
            bool: True if successful
        """
        return interface.add_gallery_images(self.id, image_ids)

    @classmethod
    def from_dict(cls, data: dict) -> "StashGallery":
        """Create a StashGallery instance from a dictionary.

        Args:
            data: Dictionary containing gallery data from GraphQL or other sources.

        Returns:
            A new StashGallery instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        gallery_data = data.get("gallery", data)

        # Create the base gallery object
        gallery = cls(
            id=str(gallery_data.get("id", "")),
            urls=list(gallery_data.get("urls", [])),
            title=gallery_data.get("title"),
            code=gallery_data.get("code"),
            date=gallery_data.get("date"),
            details=gallery_data.get("details"),
            photographer=gallery_data.get("photographer"),
            rating100=gallery_data.get("rating100"),
            organized=bool(gallery_data.get("organized", False)),
            created_at=gallery_data.get("created_at"),
            updated_at=gallery_data.get("updated_at"),
        )

        # Handle files if present
        if "files" in gallery_data:
            from .stash_base_file import StashBaseFile

            gallery.files = [StashBaseFile.from_dict(f) for f in gallery_data["files"]]

        # Handle scenes if present
        if "scenes" in gallery_data:
            from .stash_scene import StashScene

            gallery.scenes = [StashScene.from_dict(s) for s in gallery_data["scenes"]]

        # Handle performers if present
        if "performers" in gallery_data:
            from .stash_performer import StashPerformer

            gallery.performers = [
                StashPerformer.from_dict(p) for p in gallery_data["performers"]
            ]

        # Handle tags if present
        if "tags" in gallery_data:
            from .stash_tag import StashTag

            gallery.tags = [StashTag.from_dict(t) for t in gallery_data["tags"]]

        # Handle studio if present
        if "studio" in gallery_data and gallery_data["studio"]:
            from .stash_studio import StashStudio

            gallery.studio = StashStudio.from_dict(gallery_data["studio"])

        # Handle cover if present
        if "cover" in gallery_data and gallery_data["cover"]:
            from .stash_image import StashImage

            gallery.cover = StashImage.from_dict(gallery_data["cover"])

        return gallery
