from datetime import datetime

from stashapi.stash_types import Gender
from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .stash_group import StashGroup
from .stash_scene import StashScene


class StashPerformer(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashPerformer":
        data = interface.find_performer(id)
        return StashPerformer.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_performer(self.to_dict())

    name: str
    disambiguation: str | None
    urls: list[str]
    gender: Gender | None
    birthdate: str | None
    ethnicity: str | None
    country: str | None
    eye_color: str | None
    height_cm: int | None
    measurements: str | None
    fake_tits: str | None
    penis_length: float | None
    circumcised: str | None
    career_length: str | None
    tattoos: str | None
    piercings: str | None
    favorite: bool
    ignore_auto_tag: bool
    image_path: str | None
    o_counter: int | None
    rating100: int | None
    details: str | None
    death_date: str | None
    hair_color: str | None
    weight: int | None
    scenes: list[StashScene]
    stash_ids: list[str]
    groups: list[StashGroup]
    custom_fields: dict[str, str]

    def __init__(
        self,
        id: str,
        urls: list[str] = [],
        name: str = "ReplaceMe",
        disambiguation: str | None = None,
        gender: Gender | None = None,
        birthdate: str | None = None,
        ethnicity: str | None = None,
        country: str | None = None,
        eye_color: str | None = None,
        height_cm: int | None = None,
        measurements: str | None = None,
        fake_tits: str | None = None,
        penis_length: float | None = None,
        circumcised: str | None = None,
        career_length: str | None = None,
        tattoos: str | None = None,
        piercings: str | None = None,
        favorite: bool = False,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        o_counter: int | None = None,
        rating100: int | None = None,
        details: str | None = None,
        death_date: str | None = None,
        hair_color: str | None = None,
        weight: int | None = None,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ) -> None:
        super().__init__(id=id, urls=urls, created_at=created_at, updated_at=updated_at)
        self.name = name
        self.disambiguation = disambiguation
        self.gender = gender
        self.birthdate = self.sanitize_datetime(birthdate)
        self.ethnicity = ethnicity
        self.country = country
        self.eye_color = eye_color
        self.height_cm = height_cm
        self.measurements = measurements
        self.fake_tits = fake_tits
        self.penis_length = penis_length
        self.circumcised = circumcised
        self.career_length = career_length
        self.tattoos = tattoos
        self.piercings = piercings
        self.alias_list = []
        self.favorite = favorite
        self.tags = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.o_counter = o_counter
        self.rating100 = rating100
        self.details = details
        self.death_date = self.sanitize_datetime(death_date)
        self.hair_color = hair_color
        self.weight = weight
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)
        self.scenes = []
        self.stash_ids = []
        self.groups = []
        self.custom_fields: dict[str, str] = {}

    def to_dict(self) -> dict:
        base_dict = super().to_dict()
        performer_dict = {
            "name": self.name,
            "disambiguation": self.disambiguation,
            "gender": self.gender.value if self.gender else None,
            "birthdate": self.birthdate.isoformat() if self.birthdate else None,
            "ethnicity": self.ethnicity,
            "country": self.country,
            "eye_color": self.eye_color,
            "height_cm": self.height_cm,
            "measurements": self.measurements,
            "fake_tits": self.fake_tits,
            "penis_length": self.penis_length,
            "circumcised": self.circumcised,
            "career_length": self.career_length,
            "tattoos": self.tattoos,
            "piercings": self.piercings,
            "favorite": self.favorite,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "o_counter": self.o_counter,
            "rating100": self.rating100,
            "details": self.details,
            "death_date": self.death_date.isoformat() if self.death_date else None,
            "hair_color": self.hair_color,
            "weight": self.weight,
            "scenes": [scene.id for scene in self.scenes],
            "stash_ids": self.stash_ids,
            "groups": [group.id for group in self.groups],
            "custom_fields": self.custom_fields,
        }
        return {**base_dict, **performer_dict}

    def scene_count(self) -> int:
        return len(self.scenes)

    def image_count(self) -> int:
        # Implement logic to count images
        return 0

    def gallery_count(self) -> int:
        # Implement logic to count galleries
        return 0

    def group_count(self) -> int:
        # Implement logic to count groups
        return 0

    def performer_count(self) -> int:
        # Implement logic to count performers
        return 0
