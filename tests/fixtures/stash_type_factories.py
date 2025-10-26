"""FactoryBoy factories for Stash API types.

This module provides factories for creating test instances of Stash API types
(Performer, Studio, Scene, etc.) using FactoryBoy. These are NOT SQLAlchemy models,
but rather Strawberry GraphQL types used to interact with the Stash API.

Usage:
    from tests.fixtures import PerformerFactory, StudioFactory

    # Create a test performer
    performer = PerformerFactory(name="Test Performer")

    # Create a performer with specific attributes
    performer = PerformerFactory(
        id="123",
        name="Jane Doe",
        gender=GenderEnum.FEMALE,
    )
"""

from factory.base import Factory
from factory.declarations import LazyFunction, Sequence

from stash.types import Gallery, Group, Image, Performer, Scene, Studio, Tag


class PerformerFactory(Factory):
    """Factory for Performer Stash API type.

    Creates Performer instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic performer
        performer = PerformerFactory()

        # Create a performer with specific values
        performer = PerformerFactory(
            id="123",
            name="Jane Doe",
            gender=GenderEnum.FEMALE,
            urls=["https://example.com/performer"]
        )
    """

    class Meta:
        model = Performer

    # Required fields
    id = Sequence(lambda n: str(100 + n))
    name = Sequence(lambda n: f"Performer_{n}")

    # Lists with default factories (required fields)
    alias_list = LazyFunction(list)
    tags = LazyFunction(list)
    stash_ids = LazyFunction(list)
    scenes = LazyFunction(list)
    groups = LazyFunction(list)
    urls = LazyFunction(list)

    # Optional fields
    disambiguation = None
    gender = None
    birthdate = None
    ethnicity = None
    country = None
    eye_color = None
    height_cm = None
    measurements = None
    fake_tits = None
    penis_length = None
    circumcised = None
    career_length = None
    tattoos = None
    piercings = None
    image_path = None
    details = None
    death_date = None
    hair_color = None
    weight = None


class StudioFactory(Factory):
    """Factory for Studio Stash API type.

    Creates Studio instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic studio
        studio = StudioFactory()

        # Create a studio with specific values
        studio = StudioFactory(
            id="456",
            name="Test Studio",
            url="https://example.com/studio"
        )
    """

    class Meta:
        model = Studio

    # Required fields
    id = Sequence(lambda n: str(200 + n))
    name = Sequence(lambda n: f"Studio_{n}")

    # Optional fields
    url = None
    parent_studio = None
    # Add other Studio fields as needed


class TagFactory(Factory):
    """Factory for Tag Stash API type.

    Creates Tag instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic tag
        tag = TagFactory()

        # Create a tag with specific values
        tag = TagFactory(
            id="789",
            name="Test Tag",
            description="A test tag description"
        )
    """

    class Meta:
        model = Tag

    # Required fields
    id = Sequence(lambda n: str(300 + n))
    name = Sequence(lambda n: f"Tag_{n}")

    # Lists with default factories (required fields)
    aliases = LazyFunction(list)
    parents = LazyFunction(list)
    children = LazyFunction(list)

    # Optional fields
    description = None
    image_path = None


class SceneFactory(Factory):
    """Factory for Scene Stash API type.

    Creates Scene instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic scene
        scene = SceneFactory()

        # Create a scene with specific values
        scene = SceneFactory(
            id="1001",
            title="Test Scene",
            studio=StudioFactory()
        )
    """

    class Meta:
        model = Scene

    # Required fields
    id = Sequence(lambda n: str(400 + n))
    title = Sequence(lambda n: f"Scene_{n}")

    # Lists with default factories
    files = LazyFunction(list)
    tags = LazyFunction(list)
    performers = LazyFunction(list)
    stash_ids = LazyFunction(list)
    groups = LazyFunction(list)
    urls = LazyFunction(list)
    galleries = LazyFunction(list)
    scene_markers = LazyFunction(list)
    sceneStreams = LazyFunction(list)
    captions = LazyFunction(list)

    # Optional fields
    code = None
    details = None
    director = None
    date = None
    rating100 = None
    o_counter = None
    organized = False
    studio = None
    paths = None


class GalleryFactory(Factory):
    """Factory for Gallery Stash API type.

    Creates Gallery instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic gallery
        gallery = GalleryFactory()

        # Create a gallery with specific values
        gallery = GalleryFactory(
            id="2001",
            title="Test Gallery",
            image_count=50
        )
    """

    class Meta:
        model = Gallery

    # Required fields
    id = Sequence(lambda n: str(500 + n))
    title = Sequence(lambda n: f"Gallery_{n}")

    # Lists with default factories
    files = LazyFunction(list)
    tags = LazyFunction(list)
    performers = LazyFunction(list)
    scenes = LazyFunction(list)
    urls = LazyFunction(list)
    chapters = LazyFunction(list)

    # Optional fields
    code = None
    date = None
    details = None
    photographer = None
    rating100 = None
    organized = False
    studio = None
    image_count = 0
    folder = None


class ImageFactory(Factory):
    """Factory for Image Stash API type.

    Creates Image instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic image
        image = ImageFactory()

        # Create an image with specific values
        image = ImageFactory(
            id="3001",
            title="Test Image",
            organized=True
        )
    """

    class Meta:
        model = Image

    # Required fields
    id = Sequence(lambda n: str(600 + n))
    title = Sequence(lambda n: f"Image_{n}")

    # Lists with default factories
    visual_files = LazyFunction(list)
    tags = LazyFunction(list)
    performers = LazyFunction(list)
    galleries = LazyFunction(list)
    urls = LazyFunction(list)

    # Optional fields
    code = None
    date = None
    details = None
    photographer = None
    organized = False
    studio = None
    paths = None


class GroupFactory(Factory):
    """Factory for Group Stash API type.

    Creates Group instances with realistic defaults for testing Stash API
    interactions without needing a real Stash server.

    Example:
        # Create a basic group
        group = GroupFactory()

        # Create a group with specific values
        group = GroupFactory(
            id="4001",
            name="Test Series",
            duration=7200
        )
    """

    class Meta:
        model = Group

    # Required fields
    id = Sequence(lambda n: str(700 + n))
    name = Sequence(lambda n: f"Group_{n}")

    # Lists with default factories
    tags = LazyFunction(list)
    scenes = LazyFunction(list)
    urls = LazyFunction(list)
    containing_groups = LazyFunction(list)
    sub_groups = LazyFunction(list)

    # Optional fields
    aliases = None
    duration = None
    date = None
    studio = None
    director = None
    synopsis = None
    front_image_path = None
    back_image_path = None


# Export all factories
__all__ = [
    "PerformerFactory",
    "StudioFactory",
    "TagFactory",
    "SceneFactory",
    "GalleryFactory",
    "ImageFactory",
    "GroupFactory",
]
