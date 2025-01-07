from datetime import datetime

from stashapi.stashapp import StashInterface

from .types import StashImageProtocol

image_fragment = (
    "id "
    "title "
    "code "
    "urls "
    "date "
    "details "
    "created_at "
    "updated_at "
    "visual_files { "
    "... on ImageFile {id path basename parent_folder_id size created_at updated_at} "
    "... on VideoFile {id path basename parent_folder_id size format created_at updated_at} "
    " } "
    "galleries { "
    "id "
    "} "
    "performers { "
    "id "
    "} "
    "studio { "
    "id "
    "} "
)


class StashImage(StashImageProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashImage":
        """Find an image by ID.

        Args:
            id: The ID of the image to find
            interface: StashInterface instance to use for querying

        Returns:
            StashImage instance if found, None otherwise
        """
        data = interface.find_image(id)
        return StashImage.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashImage"]:
        """Find all images matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashImage instances matching the criteria
        """
        data = interface.find_images(filter=filter, q=q)
        return [StashImage.from_dict(i) for i in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this image in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_image(self.to_dict())

    @staticmethod
    def update_batch(
        interface: StashInterface, images: list["StashImage"]
    ) -> list[dict]:
        """Update multiple images at once.

        Args:
            interface: StashInterface instance to use for updating
            images: List of StashImage instances to update

        Returns:
            List of updated image data from stash
        """
        updates = [i.to_update_input_dict() for i in images]
        return interface.update_images(updates)

    def __init__(
        self,
        id: str,
        title: str | None = None,
        code: str | None = None,
        rating100: int | None = None,
        urls: list[str] = [],
        date: datetime | str | None = None,
        details: str | None = None,
        photographer: str | None = None,
        o_counter: int | None = None,
        organized: bool = False,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        StashImageProtocol.__init__(
            self=self, id=id, urls=urls, created_at=created_at, updated_at=updated_at
        )
        self.title = title
        self.code = code
        self.rating100 = rating100
        self.date: datetime = date
        self.details: str = details
        self.photographer: str = photographer
        self.o_counter = o_counter
        self.organized: bool = organized
        self.visual_files = []
        self.paths = None
        self.galleries = []
        self.studio = None
        self.tags = []
        self.performers = []

    def to_dict(self) -> dict:
        """Convert the image object to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "code": self.code,
            "rating100": self.rating100,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "o_counter": self.o_counter,
            "organized": self.organized,
            "visual_files": [file.to_dict() for file in self.visual_files],
            "galleries": [gallery.id for gallery in self.galleries],
            "studio": self.studio.id if self.studio else None,
            "tags": [tag.id for tag in self.tags],
            "performers": [performer.id for performer in self.performers],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the StashImage object into a dictionary matching the ImageCreateInput GraphQL definition."""
        return {
            "title": self.title,
            "code": self.code,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "rating100": self.rating100,
            "organized": self.organized,
            "gallery_ids": [gallery.id for gallery in self.galleries],
            "studio_id": self.studio.id if self.studio else None,
            "tag_ids": [tag.id for tag in self.tags],
            "performer_ids": [performer.id for performer in self.performers],
        }

    def to_update_input_dict(self) -> dict:
        """Converts the StashImage object into a dictionary matching the ImageUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the image in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created image data
        """
        return interface.create_image(
            self.visual_files[0].get_path() if self.visual_files else ""
        )

    @classmethod
    def from_dict(cls, data: dict) -> "StashImage":
        """Create a StashImage instance from a dictionary.

        Args:
            data: Dictionary containing image data from GraphQL or other sources.

        Returns:
            A new StashImage instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        image_data = data.get("image", data)

        # Create the base image object
        image = cls(
            id=str(image_data.get("id", "")),
            title=image_data.get("title"),
            code=image_data.get("code"),
            rating100=image_data.get("rating100"),
            urls=list(image_data.get("urls", [])),
            date=image_data.get("date"),
            details=image_data.get("details"),
            photographer=image_data.get("photographer"),
            o_counter=image_data.get("o_counter"),
            organized=bool(image_data.get("organized", False)),
            created_at=image_data.get("created_at"),
            updated_at=image_data.get("updated_at"),
        )

        # Handle visual_files if present
        if "visual_files" in image_data:
            from .visual_file import VisualFile

            image.visual_files = [
                VisualFile.from_dict(f) for f in image_data["visual_files"]
            ]

        # Handle galleries if present
        if "galleries" in image_data:
            from .stash_gallery import StashGallery

            image.galleries = [
                StashGallery.from_dict(g) for g in image_data["galleries"]
            ]

        # Handle studio if present
        if "studio" in image_data and image_data["studio"]:
            from .stash_studio import StashStudio

            image.studio = StashStudio.from_dict(image_data["studio"])

        # Handle tags if present
        if "tags" in image_data:
            from .stash_tag import StashTag

            image.tags = [StashTag.from_dict(t) for t in image_data["tags"]]

        # Handle performers if present
        if "performers" in image_data:
            from .stash_performer import StashPerformer

            image.performers = [
                StashPerformer.from_dict(p) for p in image_data["performers"]
            ]

        return image
