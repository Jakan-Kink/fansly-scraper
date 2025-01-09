"""Tests for Performer dataclass."""

from datetime import datetime, timezone

import pytest
from stashapi.stash_types import Gender

from stash.performer import Performer


def test_performer_creation():
    """Test basic Performer creation."""
    now = datetime.now(timezone.utc)
    performer = Performer(
        id="123",
        name="Test Performer",
        urls=["http://example.com"],
        disambiguation="Test disambiguation",
        gender=Gender.FEMALE,
        birthdate=now,
        ethnicity="Test ethnicity",
        country="Test country",
        eye_color="Blue",
        height_cm=170,
        measurements="34-24-36",
        fake_tits="No",
        career_length="10 years",
        tattoos="None",
        piercings="Ears",
        favorite=True,
        ignore_auto_tag=False,
        image_path="/image/path",
        o_counter=5,
        rating100=75,
        details="Test details",
        death_date=None,
        hair_color="Brown",
        weight=60,
        created_at=now,
        updated_at=now,
    )

    assert performer.id == "123"
    assert performer.name == "Test Performer"
    assert performer.urls == ["http://example.com"]
    assert performer.disambiguation == "Test disambiguation"
    assert performer.gender == Gender.FEMALE
    assert performer.birthdate == now
    assert performer.ethnicity == "Test ethnicity"
    assert performer.country == "Test country"
    assert performer.eye_color == "Blue"
    assert performer.height_cm == 170
    assert performer.measurements == "34-24-36"
    assert performer.fake_tits == "No"
    assert performer.career_length == "10 years"
    assert performer.tattoos == "None"
    assert performer.piercings == "Ears"
    assert performer.favorite is True
    assert performer.ignore_auto_tag is False
    assert performer.image_path == "/image/path"
    assert performer.o_counter == 5
    assert performer.rating100 == 75
    assert performer.details == "Test details"
    assert performer.death_date is None
    assert performer.hair_color == "Brown"
    assert performer.weight == 60
    assert performer.created_at == now
    assert performer.updated_at == now


def test_performer_to_dict():
    """Test Performer to_dict method."""
    now = datetime.now(timezone.utc)
    performer = Performer(
        id="123",
        name="Test Performer",
        urls=["http://example.com"],
        gender=Gender.FEMALE,
        birthdate=now,
        height_cm=170,
        measurements="34-24-36",
        favorite=True,
        rating100=75,
        created_at=now,
        updated_at=now,
    )

    data = performer.to_dict()
    assert data["id"] == "123"
    assert data["name"] == "Test Performer"
    assert data["urls"] == ["http://example.com"]
    assert data["gender"] == "FEMALE"
    assert data["birthdate"] == now.isoformat()
    assert data["height_cm"] == 170
    assert data["measurements"] == "34-24-36"
    assert data["favorite"] is True
    assert data["rating100"] == 75
    assert data["created_at"] == now.isoformat()
    assert data["updated_at"] == now.isoformat()


def test_performer_from_dict():
    """Test Performer from_dict method."""
    now = datetime.now(timezone.utc)
    data = {
        "id": "123",
        "name": "Test Performer",
        "urls": ["http://example.com"],
        "gender": "FEMALE",
        "birthdate": now.isoformat(),
        "height_cm": 170,
        "measurements": "34-24-36",
        "favorite": True,
        "rating100": 75,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    performer = Performer.from_dict(data)
    assert performer.id == "123"
    assert performer.name == "Test Performer"
    assert performer.urls == ["http://example.com"]
    assert performer.gender == Gender.FEMALE
    assert performer.birthdate == now
    assert performer.height_cm == 170
    assert performer.measurements == "34-24-36"
    assert performer.favorite is True
    assert performer.rating100 == 75
    assert performer.created_at == now
    assert performer.updated_at == now
