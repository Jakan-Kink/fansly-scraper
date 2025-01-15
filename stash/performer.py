from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from stashapi.stash_types import Gender

from .stash_interface import StashInterface
from .types import (
    StashGroupProtocol,
    StashPerformerProtocol,
    StashSceneProtocol,
    StashTagProtocol,
)


@dataclass
class Performer(StashPerformerProtocol):
    id: str
    name: str
    urls: list[str] = field(default_factory=list)
    disambiguation: str | None = None
    gender: Gender | None = None
    birthdate: datetime | None = None
    ethnicity: str | None = None
    country: str | None = None
    eye_color: str | None = None
    height_cm: int | None = None
    measurements: str | None = None
    fake_tits: str | None = None
    penis_length: float | None = None
    circumcised: str | None = None
    career_length: str | None = None
    tattoos: str | None = None
    piercings: str | None = None
    favorite: bool = False
    ignore_auto_tag: bool = False
    image_path: str | None = None
    o_counter: int | None = None
    rating100: int | None = None
    details: str | None = None
    death_date: datetime | None = None
    hair_color: str | None = None
    weight: int | None = None
    scenes: list[StashSceneProtocol] = field(default_factory=list)
    stash_ids: list[str] = field(default_factory=list)
    groups: list[StashGroupProtocol] = field(default_factory=list)
    custom_fields: dict[str, str] = field(default_factory=dict)
    tags: list[StashTagProtocol] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def find(id: str, interface: StashInterface) -> Performer | None:
        """Find a performer by ID.

        Args:
            id: The ID of the performer to find
            interface: StashInterface instance to use for querying

        Returns:
            Performer instance if found, None otherwise
        """
        data = interface.find_performer(id)
        return Performer.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Performer]:
        """Find all performers matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Performer instances matching the criteria
        """
        data = interface.find_performers(filter=filter, q=q)
        return [Performer.from_dict(p) for p in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this performer in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_performer(self.to_update_input_dict())

    @staticmethod
    def create_batch(
        interface: StashInterface, performers: list[Performer]
    ) -> list[dict]:
        """Create multiple performers at once.

        Args:
            interface: StashInterface instance to use for creation
            performers: List of Performer instances to create

        Returns:
            List of created performer data from stash
        """
        inputs = [p.to_create_input_dict() for p in performers]
        return interface.create_performers(inputs)

    @staticmethod
    def update_batch(
        interface: StashInterface, performers: list[Performer]
    ) -> list[dict]:
        """Update multiple performers at once.

        Args:
            interface: StashInterface instance to use for updating
            performers: List of Performer instances to update

        Returns:
            List of updated performer data from stash
        """
        updates = [p.to_update_input_dict() for p in performers]
        return interface.update_performers(updates)

    def to_dict(self) -> dict:
        """Convert the performer object to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "urls": self.urls,
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
            "scenes": [s.to_dict() for s in self.scenes],
            "stash_ids": self.stash_ids,
            "groups": [g.to_dict() for g in self.groups],
            "custom_fields": self.custom_fields,
            "tags": [t.to_dict() for t in self.tags],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    _input_fields = {
        "name": ("name", None, None, True),
        "urls": ("urls", [], None, False),
        "disambiguation": ("disambiguation", None, None, False),
        "gender": ("gender", None, lambda x: x.value if x else None, False),
        "birthdate": (
            "birthdate",
            None,
            lambda x: x.date().isoformat() if x else None,
            False,
        ),
        "ethnicity": ("ethnicity", None, None, False),
        "country": ("country", None, None, False),
        "eye_color": ("eye_color", None, None, False),
        "height_cm": ("height_cm", None, None, False),
        "measurements": ("measurements", None, None, False),
        "fake_tits": ("fake_tits", None, None, False),
        "penis_length": ("penis_length", None, None, False),
        "circumcised": ("circumcised", None, None, False),
        "career_length": ("career_length", None, None, False),
        "tattoos": ("tattoos", None, None, False),
        "piercings": ("piercings", None, None, False),
        "favorite": ("favorite", False, None, False),
        "ignore_auto_tag": ("ignore_auto_tag", False, None, False),
        "image_path": ("image_path", None, None, False),
        "o_counter": ("o_counter", None, None, False),
        "rating100": ("rating100", None, None, False),
        "details": ("details", None, None, False),
        "death_date": (
            "death_date",
            None,
            lambda x: x.date().isoformat() if x else None,
            False,
        ),
        "hair_color": ("hair_color", None, None, False),
        "weight": ("weight", None, None, False),
        "tag_ids": ("tags", [], lambda x: [t.id for t in x], False),
        "stash_ids": ("stash_ids", [], None, False),
        "custom_fields": ("custom_fields", {}, None, False),
    }

    def to_create_input_dict(self) -> dict:
        """Converts the Performer object into a dictionary matching the PerformerCreateInput GraphQL definition.

        Only includes fields that have non-default values to prevent unintended overwrites.
        Uses _input_fields configuration to determine what to include.
        """
        result = {}

        for field_name, (
            attr_name,
            default_value,
            transform_func,
            required,
        ) in self._input_fields.items():
            value = getattr(self, attr_name)

            # Skip None values for non-required fields
            if value is None and not required:
                continue

            # Skip if value equals default (but still include required fields)
            if not required and value == default_value:
                continue

            # For empty lists (but still include required fields)
            if not required and isinstance(default_value, list) and not value:
                continue

            # Special handling for numeric fields that could be 0
            if isinstance(value, (int, float)) or value is not None:
                result[field_name] = transform_func(value) if transform_func else value

        return result

    def to_update_input_dict(self) -> dict:
        """Converts the Performer object into a dictionary matching the PerformerUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the performer in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created performer data
        """
        return interface.create_performer(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Performer:
        """Create a Performer instance from a dictionary.

        Args:
            data: Dictionary containing performer data from GraphQL or other sources.

        Returns:
            A new Performer instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        performer_data = data.get("performer", data)

        from .stash_context import StashQL

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        birthdate = StashQL.sanitize_datetime(performer_data.get("birthdate"))
        death_date = StashQL.sanitize_datetime(performer_data.get("death_date"))
        created_at = StashQL.sanitize_datetime(performer_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(performer_data.get("updated_at"))

        # Convert gender string to enum if present
        gender = (
            Gender(performer_data["gender"]) if performer_data.get("gender") else None
        )

        # Handle relationships
        tags = []
        if "tags" in performer_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in performer_data["tags"]]

        scenes = []
        if "scenes" in performer_data:
            from .scene import Scene

            scenes = [Scene.from_dict(s) for s in performer_data["scenes"]]

        groups = []
        if "groups" in performer_data:
            from .group import Group

            groups = [Group.from_dict(g) for g in performer_data["groups"]]

        # Create the performer instance
        performer = cls(
            id=str(performer_data.get("id", "")),
            name=performer_data.get("name", None),
            urls=list(performer_data.get("urls", [])),
            disambiguation=performer_data.get("disambiguation"),
            gender=gender,
            birthdate=birthdate,
            ethnicity=performer_data.get("ethnicity"),
            country=performer_data.get("country"),
            eye_color=performer_data.get("eye_color"),
            height_cm=performer_data.get("height_cm"),
            measurements=performer_data.get("measurements"),
            fake_tits=performer_data.get("fake_tits"),
            penis_length=performer_data.get("penis_length"),
            circumcised=performer_data.get("circumcised"),
            career_length=performer_data.get("career_length"),
            tattoos=performer_data.get("tattoos"),
            piercings=performer_data.get("piercings"),
            favorite=bool(performer_data.get("favorite", False)),
            ignore_auto_tag=bool(performer_data.get("ignore_auto_tag", False)),
            image_path=performer_data.get("image_path"),
            o_counter=performer_data.get("o_counter"),
            rating100=performer_data.get("rating100"),
            details=performer_data.get("details"),
            death_date=death_date,
            hair_color=performer_data.get("hair_color"),
            weight=performer_data.get("weight"),
            scenes=scenes,
            stash_ids=list(performer_data.get("stash_ids", [])),
            groups=groups,
            custom_fields=dict(performer_data.get("custom_fields", {})),
            tags=tags,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return performer
