from datetime import datetime

from .gender_enum import GenderEnum


class StashPerformer:
    def __init__(
        self,
        id: str,
        name: str,
        disambiguation: str | None = None,
        urls: list[str] = [],
        gender: GenderEnum | None = None,
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
    ):
        self.id = id
        self.name = name
        self.disambiguation = disambiguation
        self.urls = urls
        self.gender = gender
        self.birthdate = birthdate
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
        self.death_date = death_date
        self.hair_color = hair_color
        self.weight = weight
        self.created_at = created_at
        self.updated_at = updated_at
        self.scenes = []
        self.stash_ids = []
        self.groups = []
        self.custom_fields: dict[str, str] = {}

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
