from datetime import datetime

from stashapi.stash_types import Gender
from stashapi.stashapp import StashInterface

from .types import StashPerformerProtocol

performer_fragment = (
    "id "
    "name "
    "disambiguation "
    "urls "
    "gender "
    "birthdate "
    "ethnicity "
    "country "
    "eye_color "
    "height_cm "
    "measurements "
    "fake_tits "
    "penis_length "
    "career_length "
    "tattoos "
    "piercings "
    "alias_list "
    "favorite "
    "tags { id name aliases } "
    "ignore_auto_tag "
    "image_path "
    "details "
    "death_date "
    "hair_color "
    "weight "
    "created_at "
    "updated_at "
)


class StashPerformer(StashPerformerProtocol):

    def __init__(
        self,
        id: str | None = None,
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

    def stash_create(self, interface: StashInterface) -> dict:
        return interface.create_performer(self.to_create_input_dict())

    def to_update_input_dict(self) -> dict:
        """
        Converts the StashPerformer object into a dictionary matching the PerformerUpdateInput GraphQL definition.
        """
        return {
            "id": self.id,
            "name": self.name,
            "disambiguation": self.disambiguation,
            "url": self.urls[0] if self.urls else None,
            "urls": self.urls,
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
            "alias_list": self.alias_list,
            "twitter": None,  # Placeholder for now
            "instagram": None,  # Placeholder for now
            "favorite": self.favorite,
            "tag_ids": [],  # Placeholder for now
            "image": self.image_path,
            "stash_ids": self.stash_ids,
            "rating100": self.rating100,
            "details": self.details,
            "death_date": self.death_date.isoformat() if self.death_date else None,
            "hair_color": self.hair_color,
            "weight": self.weight,
            "ignore_auto_tag": self.ignore_auto_tag,
            "custom_fields": self.custom_fields,
        }

    def to_create_input_dict(self) -> dict:
        """
        This converts the StashPerformer object into a dictionary that matches the PerformerCreateInput of StashApp's GraphQL.
        """
        return {
            "name": self.name,
            "disambiguation": self.disambiguation,
            "urls": self.urls,
            "gender": self.gender.value if self.gender else None,
            "birthdate": self.birthdate.isoformat() if self.birthdate else None,
            "ethnicity": self.ethnicity,
            "country": self.country,
            "eye_color": self.eye_color,
            "height_cm": self.height_cm,
            "measurements": self.measurements,
            "fake_tits": self.fake_tits,
            "penis_length": self.penis_length,
            "career_length": self.career_length,
            "tattoos": self.tattoos,
            "piercings": self.piercings,
            "alias_list": self.alias_list,
            "twitter": None,  # Placeholder for now
            "instagram": None,  # Placeholder for now
            "favorite": self.favorite,
            "tag_ids": [],  # Placeholder for now
            "image": self.image_path,
            "stash_ids": self.stash_ids,
            "rating100": self.rating100,
            "details": self.details,
            "death_date": self.death_date.isoformat() if self.death_date else None,
            "hair_color": self.hair_color,
            "weight": self.weight,
            "ignore_auto_tag": self.ignore_auto_tag,
            "custom_fields": self.custom_fields,
        }

    def to_dict(self) -> dict:
        performer_dict = {
            "id": self.id,
            "urls": self.urls,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
        return performer_dict

    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashPerformer":
        """Find a performer by ID.

        Args:
            id: The ID of the performer to find
            interface: StashInterface instance to use for querying

        Returns:
            StashPerformer instance if found, None otherwise
        """
        data = interface.find_performer(id)
        return StashPerformer.from_dict(data) if data else None

    @staticmethod
    def find_by_name(
        name: str, interface: StashInterface, create: bool = False
    ) -> "StashPerformer":
        """Find a performer by name.

        Args:
            name: The name of the performer to find
            interface: StashInterface instance to use for querying
            create: If True, create the performer if it doesn't exist

        Returns:
            StashPerformer instance if found, None otherwise
        """
        data = interface.find_performer(name, create=create)
        return StashPerformer.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashPerformer"]:
        """Find all performers matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashPerformer instances matching the criteria
        """
        data = interface.find_performers(filter=filter, q=q)
        return [StashPerformer.from_dict(p) for p in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this performer in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_performer(self.to_update_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "StashPerformer":
        """Create a StashPerformer instance from a dictionary.

        Args:
            data: Dictionary containing performer data from GraphQL or other sources.

        Returns:
            A new StashPerformer instance.
        """
        from logging_utils import json_output

        # Log incoming data
        json_output(1, "StashPerformer.from_dict - input data", data)

        # Handle both GraphQL response format and direct dictionary format
        performer_data = data.get("performer", data)
        json_output(1, "StashPerformer.from_dict - normalized data", performer_data)

        # Convert numeric fields
        try:
            height_cm = (
                int(performer_data["height_cm"])
                if performer_data.get("height_cm")
                else None
            )
        except (ValueError, TypeError):
            height_cm = None

        try:
            weight = (
                int(performer_data["weight"]) if performer_data.get("weight") else None
            )
        except (ValueError, TypeError):
            weight = None

        try:
            penis_length = (
                float(performer_data["penis_length"])
                if performer_data.get("penis_length")
                else None
            )
        except (ValueError, TypeError):
            penis_length = None

        # Handle timestamps
        created_at = performer_data.get("created_at")
        updated_at = performer_data.get("updated_at")
        birthdate = performer_data.get("birthdate")
        death_date = performer_data.get("death_date")

        # Extract basic fields with defaults and type conversions
        performer = cls(
            id=str(performer_data.get("id", "")),
            name=str(performer_data.get("name", "")),
            urls=list(performer_data.get("urls", [])),
            disambiguation=performer_data.get("disambiguation"),
            gender=(
                Gender(performer_data["gender"])
                if performer_data.get("gender")
                else None
            ),
            birthdate=birthdate,  # __init__ will handle datetime conversion
            ethnicity=performer_data.get("ethnicity"),
            country=performer_data.get("country"),
            eye_color=performer_data.get("eye_color"),
            height_cm=height_cm,
            measurements=performer_data.get("measurements"),
            fake_tits=performer_data.get("fake_tits"),
            penis_length=penis_length,
            career_length=performer_data.get("career_length"),
            tattoos=performer_data.get("tattoos"),
            piercings=performer_data.get("piercings"),
            favorite=bool(performer_data.get("favorite", False)),
            ignore_auto_tag=bool(performer_data.get("ignore_auto_tag", False)),
            image_path=performer_data.get("image_path"),
            details=performer_data.get("details"),
            death_date=death_date,  # __init__ will handle datetime conversion
            hair_color=performer_data.get("hair_color"),
            weight=weight,
            created_at=created_at,  # __init__ will handle datetime conversion
            updated_at=updated_at,  # __init__ will handle datetime conversion
        )

        # Handle aliases
        performer.alias_list = list(performer_data.get("alias_list", []))
        if "aliases" in performer_data:
            performer.alias_list.extend(performer_data["aliases"])

        # Log the final performer object
        json_output(
            1, "StashPerformer.from_dict - created performer", performer.to_dict()
        )

        return performer
