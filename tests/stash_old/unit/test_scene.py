"""Tests for Scene dataclass."""

from datetime import datetime, timezone

import pytest

from stash.scene import Scene, SceneFileType, ScenePathsType, SceneStreamEndpoint


def test_scene_creation():
    """Test basic Scene creation."""
    now = datetime.now(timezone.utc)
    scene = Scene(
        id="123",
        title="Test Scene",
        code="SCN123",
        details="Test Details",
        director="Test Director",
        urls=["http://example.com"],
        date=now,
        rating100=75,
        organized=True,
        o_counter=5,
        interactive=True,
        interactive_speed=100,
        created_at=now,
        updated_at=now,
        last_played_at=now,
        resume_time=120.5,
        play_duration=3600.0,
        play_count=10,
        play_history=[now],
        o_history=[now],
        paths=ScenePathsType(
            screenshot="/screenshot/path",
            preview="/preview/path",
            stream="/stream/path",
            webp="/webp/path",
            vtt="/vtt/path",
            sprite="/sprite/path",
            funscript="/funscript/path",
            interactive_heatmap="/heatmap/path",
            caption="/caption/path",
        ),
    )

    assert scene.id == "123"
    assert scene.title == "Test Scene"
    assert scene.code == "SCN123"
    assert scene.details == "Test Details"
    assert scene.director == "Test Director"
    assert scene.urls == ["http://example.com"]
    assert scene.date == now
    assert scene.rating100 == 75
    assert scene.organized is True
    assert scene.o_counter == 5
    assert scene.interactive is True
    assert scene.interactive_speed == 100
    assert scene.created_at == now
    assert scene.updated_at == now
    assert scene.last_played_at == now
    assert scene.resume_time == 120.5
    assert scene.play_duration == 3600.0
    assert scene.play_count == 10
    assert scene.play_history == [now]
    assert scene.o_history == [now]
    assert scene.paths.screenshot == "/screenshot/path"
    assert scene.paths.preview == "/preview/path"
    assert scene.paths.stream == "/stream/path"
    assert scene.paths.webp == "/webp/path"
    assert scene.paths.vtt == "/vtt/path"
    assert scene.paths.sprite == "/sprite/path"
    assert scene.paths.funscript == "/funscript/path"
    assert scene.paths.interactive_heatmap == "/heatmap/path"
    assert scene.paths.caption == "/caption/path"


def test_scene_to_dict():
    """Test Scene to_dict method."""
    now = datetime.now(timezone.utc)
    scene = Scene(
        id="123",
        title="Test Scene",
        code="SCN123",
        details="Test Details",
        director="Test Director",
        urls=["http://example.com"],
        date=now,
        rating100=75,
        organized=True,
        o_counter=5,
        interactive=True,
        interactive_speed=100,
        created_at=now,
        updated_at=now,
        last_played_at=now,
        resume_time=120.5,
        play_duration=3600.0,
        play_count=10,
        play_history=[now],
        o_history=[now],
    )

    data = scene.to_dict()
    assert data["id"] == "123"
    assert data["title"] == "Test Scene"
    assert data["code"] == "SCN123"
    assert data["details"] == "Test Details"
    assert data["director"] == "Test Director"
    assert data["urls"] == ["http://example.com"]
    assert data["date"] == now.isoformat()
    assert data["rating100"] == 75
    assert data["organized"] is True
    assert data["o_counter"] == 5
    assert data["interactive"] is True
    assert data["interactive_speed"] == 100
    assert data["created_at"] == now.isoformat()
    assert data["updated_at"] == now.isoformat()
    assert data["last_played_at"] == now.isoformat()
    assert data["resume_time"] == 120.5
    assert data["play_duration"] == 3600.0
    assert data["play_count"] == 10
    assert data["play_history"] == [now.isoformat()]
    assert data["o_history"] == [now.isoformat()]


def test_scene_from_dict():
    """Test Scene from_dict method."""
    now = datetime.now(timezone.utc)
    data = {
        "id": "123",
        "title": "Test Scene",
        "code": "SCN123",
        "details": "Test Details",
        "director": "Test Director",
        "urls": ["http://example.com"],
        "date": now.isoformat(),
        "rating100": 75,
        "organized": True,
        "o_counter": 5,
        "interactive": True,
        "interactive_speed": 100,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "last_played_at": now.isoformat(),
        "resume_time": 120.5,
        "play_duration": 3600.0,
        "play_count": 10,
        "play_history": [now.isoformat()],
        "o_history": [now.isoformat()],
    }

    scene = Scene.from_dict(data)
    assert scene.id == "123"
    assert scene.title == "Test Scene"
    assert scene.code == "SCN123"
    assert scene.details == "Test Details"
    assert scene.director == "Test Director"
    assert scene.urls == ["http://example.com"]
    assert scene.date == now
    assert scene.rating100 == 75
    assert scene.organized is True
    assert scene.o_counter == 5
    assert scene.interactive is True
    assert scene.interactive_speed == 100
    assert scene.created_at == now
    assert scene.updated_at == now
    assert scene.last_played_at == now
    assert scene.resume_time == 120.5
    assert scene.play_duration == 3600.0
    assert scene.play_count == 10
    assert scene.play_history == [now]
    assert scene.o_history == [now]


def test_scene_file_type():
    """Test SceneFileType dataclass."""
    file_type = SceneFileType(
        size="1.2GB",
        duration=3600.0,
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        framerate=30.0,
        bitrate=5000,
    )

    assert file_type.size == "1.2GB"
    assert file_type.duration == 3600.0
    assert file_type.video_codec == "h264"
    assert file_type.audio_codec == "aac"
    assert file_type.width == 1920
    assert file_type.height == 1080
    assert file_type.framerate == 30.0
    assert file_type.bitrate == 5000


def test_scene_stream_endpoint():
    """Test SceneStreamEndpoint dataclass."""
    endpoint = SceneStreamEndpoint(
        url="http://example.com/stream",
        mime_type="video/mp4",
        label="1080p",
    )

    assert endpoint.url == "http://example.com/stream"
    assert endpoint.mime_type == "video/mp4"
    assert endpoint.label == "1080p"
