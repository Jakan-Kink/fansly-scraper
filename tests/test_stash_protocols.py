"""Tests for stash module protocols."""

from datetime import datetime
from typing import Any
from unittest import TestCase

from stashapi.stash_types import Gender

from stash.base_protocols import (
    BaseFileProtocol,
    ImageFileProtocol,
    StashBaseProtocol,
    StashContentProtocol,
    StashGalleryProtocol,
    StashGroupDescriptionProtocol,
    StashGroupProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
    VideoFileProtocol,
    VisualFileProtocol,
    VisualFileType,
)


class MockStashBase:
    """Mock implementation of StashBaseProtocol."""

    def __init__(self, id: str, created_at: datetime, updated_at: datetime):
        self.id = id
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MockStashBase":
        return cls(
            id=data["id"],
            created_at=cls.sanitize_datetime(data["created_at"]),
            updated_at=cls.sanitize_datetime(data["updated_at"]),
        )

    @staticmethod
    def sanitize_datetime(value: str | datetime | None) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return None

    def save(self, interface: Any) -> None:
        pass

    @staticmethod
    def find(id: str, interface: Any) -> "MockStashBase | None":
        return None


class MockPerformer(MockStashBase):
    """Mock implementation of StashPerformerProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        name: str,
        gender: Gender | None = None,
        birthdate: datetime | None = None,
        details: str | None = None,
        favorite: bool = False,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        rating100: int | None = None,
    ):
        super().__init__(id, created_at, updated_at)
        self.name = name
        self.gender = gender
        self.birthdate = birthdate
        self.details = details
        self.favorite = favorite
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.rating100 = rating100
        # Initialize other required fields with None
        self.disambiguation = None
        self.ethnicity = None
        self.country = None
        self.eye_color = None
        self.height_cm = None
        self.measurements = None
        self.fake_tits = None
        self.penis_length = None
        self.circumcised = None
        self.career_length = None
        self.tattoos = None
        self.piercings = None
        self.o_counter = None
        self.death_date = None
        self.hair_color = None
        self.weight = None
        self.custom_fields = {}


class MockStudio(MockStashBase):
    """Mock implementation of StashStudioProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        name: str,
        url: str | None = None,
        parent_studio: "MockStudio | None" = None,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        rating100: int | None = None,
        favorite: bool = False,
        details: str | None = None,
    ):
        super().__init__(id, created_at, updated_at)
        self.name = name
        self.url = url
        self.parent_studio = parent_studio
        self.child_studios: list[StashStudioProtocol] = []
        self.aliases: list[str] = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.rating100 = rating100
        self.favorite = favorite
        self.details = details


class MockTag(MockStashBase):
    """Mock implementation of StashTagProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        name: str,
        description: str | None = None,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        favorite: bool = False,
    ):
        super().__init__(id, created_at, updated_at)
        self.name = name
        self.description = description
        self.aliases: list[str] = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.favorite = favorite
        self.parents: list[StashTagProtocol] = []
        self.children: list[StashTagProtocol] = []


class MockScene(MockStashBase):
    """Mock implementation of StashSceneProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        title: str | None = None,
        code: str | None = None,
        details: str | None = None,
        date: datetime | None = None,
        rating100: int | None = None,
        organized: bool = False,
        director: str | None = None,
        o_counter: int | None = None,
        interactive: bool = False,
        interactive_speed: int | None = None,
    ):
        super().__init__(id, created_at, updated_at)
        self.title = title
        self.code = code
        self.details = details
        self.date = date
        self.rating100 = rating100
        self.organized = organized
        self.urls: list[str] = []
        self.studio: StashStudioProtocol | None = None
        self.tags: list[StashTagProtocol] = []
        self.performers: list[StashPerformerProtocol] = []
        self.director = director
        self.o_counter = o_counter
        self.interactive = interactive
        self.interactive_speed = interactive_speed
        self.captions: list[str] = []
        self.last_played_at: datetime | None = None
        self.resume_time: float | None = None
        self.play_duration: float | None = None
        self.play_count: int | None = None
        self.play_history: list[datetime] = []
        self.o_history: list[datetime] = []


class MockImage(MockStashBase):
    """Mock implementation of StashImageProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        title: str | None = None,
        code: str | None = None,
        details: str | None = None,
        date: datetime | None = None,
        rating100: int | None = None,
        organized: bool = False,
        photographer: str | None = None,
        o_counter: int | None = None,
    ):
        super().__init__(id, created_at, updated_at)
        self.title = title
        self.code = code
        self.details = details
        self.date = date
        self.rating100 = rating100
        self.organized = organized
        self.urls: list[str] = []
        self.studio: StashStudioProtocol | None = None
        self.tags: list[StashTagProtocol] = []
        self.performers: list[StashPerformerProtocol] = []
        self.photographer = photographer
        self.o_counter = o_counter


class MockGallery(MockStashBase):
    """Mock implementation of StashGalleryProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        title: str | None = None,
        code: str | None = None,
        details: str | None = None,
        date: datetime | None = None,
        rating100: int | None = None,
        organized: bool = False,
        photographer: str | None = None,
        o_counter: int | None = None,
        image_count: int = 0,
    ):
        super().__init__(id, created_at, updated_at)
        self.title = title
        self.code = code
        self.details = details
        self.date = date
        self.rating100 = rating100
        self.organized = organized
        self.urls: list[str] = []
        self.studio: StashStudioProtocol | None = None
        self.tags: list[StashTagProtocol] = []
        self.performers: list[StashPerformerProtocol] = []
        self.photographer = photographer
        self.o_counter = o_counter
        self.image_count = image_count
        self.scenes: list[StashSceneProtocol] = []


class MockGroup(MockStashBase):
    """Mock implementation of StashGroupProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        name: str,
        aliases: str | None = None,
        duration: int | None = None,
        date: str | None = None,
        rating100: int | None = None,
        director: str | None = None,
        synopsis: str | None = None,
        front_image_path: str | None = None,
        back_image_path: str | None = None,
    ):
        super().__init__(id, created_at, updated_at)
        self.name = name
        self.aliases = aliases
        self.duration = duration
        self.date = date
        self.rating100 = rating100
        self.director = director
        self.synopsis = synopsis
        self.front_image_path = front_image_path
        self.back_image_path = back_image_path
        self.studio: StashStudioProtocol | None = None
        self.scenes: list[StashSceneProtocol] = []
        self.performers: list[StashPerformerProtocol] = []
        self.galleries: list[StashGalleryProtocol] = []
        self.images: list[StashImageProtocol] = []


class MockGroupDescription(MockStashBase):
    """Mock implementation of StashGroupDescriptionProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        containing_group: StashGroupProtocol,
        sub_group: StashGroupProtocol,
        description: str,
    ):
        super().__init__(id, created_at, updated_at)
        self.containing_group = containing_group
        self.sub_group = sub_group
        self.description = description


class MockVideoFile(MockStashBase):
    """Mock implementation of VideoFileProtocol."""

    def __init__(
        self,
        id: str,
        created_at: datetime,
        updated_at: datetime,
        path: str,
        basename: str,
        mod_time: datetime,
        size: int,
        width: int,
        height: int,
        duration: float,
        video_codec: str,
        audio_codec: str,
        frame_rate: float,
        bit_rate: int,
    ):
        super().__init__(id, created_at, updated_at)
        self.path = path
        self.basename = basename
        self.parent_folder_id = None
        self.mod_time = mod_time
        self.size = size
        self.zip_file_id = None
        self.fingerprints: list[str] = []
        self.width = width
        self.height = height
        self.duration = duration
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.frame_rate = frame_rate
        self.bit_rate = bit_rate


class TestStashProtocols(TestCase):
    """Test cases for stash protocols."""

    def setUp(self):
        """Set up test data."""
        self.now = datetime.now()
        self.base = MockStashBase("1", self.now, self.now)
        self.performer = MockPerformer(
            "2",
            self.now,
            self.now,
            "Test Performer",
            gender=Gender.FEMALE,
            birthdate=datetime(1990, 1, 1),
            details="Test details",
            favorite=True,
            ignore_auto_tag=False,
            image_path="/path/to/image.jpg",
            rating100=85,
        )
        self.studio = MockStudio(
            "3",
            self.now,
            self.now,
            "Test Studio",
            url="https://example.com",
            ignore_auto_tag=False,
            image_path="/path/to/studio.jpg",
            rating100=90,
            favorite=True,
            details="Studio details",
        )
        self.tag = MockTag(
            "4",
            self.now,
            self.now,
            "Test Tag",
            description="Tag description",
            ignore_auto_tag=False,
            image_path="/path/to/tag.jpg",
            favorite=True,
        )
        self.scene = MockScene(
            "5",
            self.now,
            self.now,
            title="Test Scene",
            code="SC123",
            details="Scene details",
            date=self.now,
            rating100=95,
            organized=True,
            director="Test Director",
            o_counter=5,
            interactive=True,
            interactive_speed=100,
        )
        self.image = MockImage(
            "6",
            self.now,
            self.now,
            title="Test Image",
            code="IMG123",
            details="Image details",
            date=self.now,
            rating100=80,
            organized=True,
            photographer="Test Photographer",
            o_counter=2,
        )
        self.gallery = MockGallery(
            "7",
            self.now,
            self.now,
            title="Test Gallery",
            code="GAL123",
            details="Gallery details",
            date=self.now,
            rating100=85,
            organized=True,
            photographer="Test Photographer",
            o_counter=3,
            image_count=10,
        )
        self.group = MockGroup(
            "8",
            self.now,
            self.now,
            name="Test Group",
            aliases="Group Alias",
            duration=3600,  # 1 hour
            date="2024-01-01",
            rating100=90,
            director="Test Director",
            synopsis="Group synopsis",
            front_image_path="/path/to/front.jpg",
            back_image_path="/path/to/back.jpg",
        )
        self.group_description = MockGroupDescription(
            "9",
            self.now,
            self.now,
            containing_group=self.group,
            sub_group=MockGroup(
                "10",
                self.now,
                self.now,
                name="Sub Group",
            ),
            description="Group description",
        )
        self.video_file = MockVideoFile(
            "11",
            self.now,
            self.now,
            "/path/to/video.mp4",
            "video.mp4",
            self.now,
            1024 * 1024,  # 1MB
            1920,
            1080,
            120.0,  # 2 minutes
            "h264",
            "aac",
            30.0,
            5000000,  # 5Mbps
        )

    def test_stash_base_protocol(self):
        """Test StashBaseProtocol implementation."""
        self.assertIsInstance(self.base, StashBaseProtocol)
        self.assertEqual(self.base.id, "1")
        self.assertEqual(self.base.created_at, self.now)
        self.assertEqual(self.base.updated_at, self.now)

        # Test to_dict and from_dict
        data = self.base.to_dict()
        reconstructed = MockStashBase.from_dict(data)
        self.assertEqual(reconstructed.id, self.base.id)
        self.assertEqual(reconstructed.created_at, self.base.created_at)
        self.assertEqual(reconstructed.updated_at, self.base.updated_at)

    def test_stash_performer_protocol(self):
        """Test StashPerformerProtocol implementation."""
        self.assertIsInstance(self.performer, StashPerformerProtocol)
        self.assertEqual(self.performer.name, "Test Performer")
        self.assertEqual(self.performer.gender, Gender.FEMALE)
        self.assertEqual(self.performer.birthdate, datetime(1990, 1, 1))
        self.assertEqual(self.performer.details, "Test details")
        self.assertTrue(self.performer.favorite)
        self.assertFalse(self.performer.ignore_auto_tag)
        self.assertEqual(self.performer.image_path, "/path/to/image.jpg")
        self.assertEqual(self.performer.rating100, 85)

    def test_datetime_sanitization(self):
        """Test datetime sanitization."""
        # Test with datetime object
        dt = datetime.now()
        self.assertEqual(MockStashBase.sanitize_datetime(dt), dt)

        # Test with ISO format string
        dt_str = "2024-01-01T12:00:00"
        expected = datetime(2024, 1, 1, 12, 0)
        self.assertEqual(MockStashBase.sanitize_datetime(dt_str), expected)

        # Test with None
        self.assertIsNone(MockStashBase.sanitize_datetime(None))

    def test_stash_studio_protocol(self):
        """Test StashStudioProtocol implementation."""
        self.assertIsInstance(self.studio, StashStudioProtocol)
        self.assertEqual(self.studio.name, "Test Studio")
        self.assertEqual(self.studio.url, "https://example.com")
        self.assertIsNone(self.studio.parent_studio)
        self.assertEqual(self.studio.child_studios, [])
        self.assertEqual(self.studio.aliases, [])
        self.assertFalse(self.studio.ignore_auto_tag)
        self.assertEqual(self.studio.image_path, "/path/to/studio.jpg")
        self.assertEqual(self.studio.rating100, 90)
        self.assertTrue(self.studio.favorite)
        self.assertEqual(self.studio.details, "Studio details")

    def test_stash_tag_protocol(self):
        """Test StashTagProtocol implementation."""
        self.assertIsInstance(self.tag, StashTagProtocol)
        self.assertEqual(self.tag.name, "Test Tag")
        self.assertEqual(self.tag.description, "Tag description")
        self.assertEqual(self.tag.aliases, [])
        self.assertFalse(self.tag.ignore_auto_tag)
        self.assertEqual(self.tag.image_path, "/path/to/tag.jpg")
        self.assertTrue(self.tag.favorite)
        self.assertEqual(self.tag.parents, [])
        self.assertEqual(self.tag.children, [])

    def test_video_file_protocol(self):
        """Test VideoFileProtocol implementation."""
        self.assertIsInstance(self.video_file, VideoFileProtocol)
        self.assertEqual(self.video_file.path, "/path/to/video.mp4")
        self.assertEqual(self.video_file.basename, "video.mp4")
        self.assertIsNone(self.video_file.parent_folder_id)
        self.assertEqual(self.video_file.mod_time, self.now)
        self.assertEqual(self.video_file.size, 1024 * 1024)
        self.assertIsNone(self.video_file.zip_file_id)
        self.assertEqual(self.video_file.fingerprints, [])
        self.assertEqual(self.video_file.width, 1920)
        self.assertEqual(self.video_file.height, 1080)
        self.assertEqual(self.video_file.duration, 120.0)
        self.assertEqual(self.video_file.video_codec, "h264")
        self.assertEqual(self.video_file.audio_codec, "aac")
        self.assertEqual(self.video_file.frame_rate, 30.0)
        self.assertEqual(self.video_file.bit_rate, 5000000)

    def test_stash_scene_protocol(self):
        """Test StashSceneProtocol implementation."""
        self.assertIsInstance(self.scene, StashSceneProtocol)
        self.assertEqual(self.scene.title, "Test Scene")
        self.assertEqual(self.scene.code, "SC123")
        self.assertEqual(self.scene.details, "Scene details")
        self.assertEqual(self.scene.date, self.now)
        self.assertEqual(self.scene.rating100, 95)
        self.assertTrue(self.scene.organized)
        self.assertEqual(self.scene.urls, [])
        self.assertIsNone(self.scene.studio)
        self.assertEqual(self.scene.tags, [])
        self.assertEqual(self.scene.performers, [])
        self.assertEqual(self.scene.director, "Test Director")
        self.assertEqual(self.scene.o_counter, 5)
        self.assertTrue(self.scene.interactive)
        self.assertEqual(self.scene.interactive_speed, 100)
        self.assertEqual(self.scene.captions, [])
        self.assertIsNone(self.scene.last_played_at)
        self.assertIsNone(self.scene.resume_time)
        self.assertIsNone(self.scene.play_duration)
        self.assertIsNone(self.scene.play_count)
        self.assertEqual(self.scene.play_history, [])
        self.assertEqual(self.scene.o_history, [])

    def test_stash_image_protocol(self):
        """Test StashImageProtocol implementation."""
        self.assertIsInstance(self.image, StashImageProtocol)
        self.assertEqual(self.image.title, "Test Image")
        self.assertEqual(self.image.code, "IMG123")
        self.assertEqual(self.image.details, "Image details")
        self.assertEqual(self.image.date, self.now)
        self.assertEqual(self.image.rating100, 80)
        self.assertTrue(self.image.organized)
        self.assertEqual(self.image.urls, [])
        self.assertIsNone(self.image.studio)
        self.assertEqual(self.image.tags, [])
        self.assertEqual(self.image.performers, [])
        self.assertEqual(self.image.photographer, "Test Photographer")
        self.assertEqual(self.image.o_counter, 2)

    def test_stash_gallery_protocol(self):
        """Test StashGalleryProtocol implementation."""
        self.assertIsInstance(self.gallery, StashGalleryProtocol)
        self.assertEqual(self.gallery.title, "Test Gallery")
        self.assertEqual(self.gallery.code, "GAL123")
        self.assertEqual(self.gallery.details, "Gallery details")
        self.assertEqual(self.gallery.date, self.now)
        self.assertEqual(self.gallery.rating100, 85)
        self.assertTrue(self.gallery.organized)
        self.assertEqual(self.gallery.urls, [])
        self.assertIsNone(self.gallery.studio)
        self.assertEqual(self.gallery.tags, [])
        self.assertEqual(self.gallery.performers, [])
        self.assertEqual(self.gallery.photographer, "Test Photographer")
        self.assertEqual(self.gallery.o_counter, 3)
        self.assertEqual(self.gallery.image_count, 10)
        self.assertEqual(self.gallery.scenes, [])

    def test_stash_group_protocol(self):
        """Test StashGroupProtocol implementation."""
        self.assertIsInstance(self.group, StashGroupProtocol)
        self.assertEqual(self.group.name, "Test Group")
        self.assertEqual(self.group.aliases, "Group Alias")
        self.assertEqual(self.group.duration, 3600)
        self.assertEqual(self.group.date, "2024-01-01")
        self.assertEqual(self.group.rating100, 90)
        self.assertEqual(self.group.director, "Test Director")
        self.assertEqual(self.group.synopsis, "Group synopsis")
        self.assertEqual(self.group.front_image_path, "/path/to/front.jpg")
        self.assertEqual(self.group.back_image_path, "/path/to/back.jpg")
        self.assertIsNone(self.group.studio)
        self.assertEqual(self.group.scenes, [])
        self.assertEqual(self.group.performers, [])
        self.assertEqual(self.group.galleries, [])
        self.assertEqual(self.group.images, [])

    def test_stash_group_description_protocol(self):
        """Test StashGroupDescriptionProtocol implementation."""
        self.assertIsInstance(self.group_description, StashGroupDescriptionProtocol)
        self.assertEqual(self.group_description.containing_group, self.group)
        self.assertEqual(self.group_description.sub_group.name, "Sub Group")
        self.assertEqual(self.group_description.description, "Group description")
