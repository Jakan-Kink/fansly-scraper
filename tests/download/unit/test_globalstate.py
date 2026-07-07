import pytest


def test_initial_state(global_state):
    """Test initial counter values."""
    assert global_state.duplicate_count == 0
    assert global_state.pic_count == 0
    assert global_state.vid_count == 0
    assert global_state.total_message_items == 0
    assert global_state.total_timeline_pictures == 0
    assert global_state.total_timeline_videos == 0


def test_total_timeline_items(global_state):
    """Test timeline items calculation."""
    global_state.total_timeline_pictures = 5
    global_state.total_timeline_videos = 3
    assert global_state.total_timeline_items() == 8


def test_total_downloaded_items(global_state):
    """Test downloaded items calculation."""
    global_state.pic_count = 10
    global_state.vid_count = 7
    assert global_state.total_downloaded_items() == 17


@pytest.mark.parametrize(
    ("state_fields", "expected_missing"),
    [
        pytest.param(
            {
                "total_timeline_pictures": 10,
                "total_timeline_videos": 5,
                "total_message_items": 3,
                "pic_count": 8,
                "vid_count": 4,
                "duplicate_count": 2,
            },
            4,
            id="normal-partial-download",
        ),
        pytest.param(
            {
                "total_timeline_pictures": 5,
                "total_timeline_videos": 5,
                "pic_count": 5,
                "vid_count": 5,
            },
            0,
            id="everything-downloaded-zero",
        ),
        pytest.param(
            {"pic_count": 10, "vid_count": 5},
            0,
            id="negative-clamped-to-zero",
        ),
    ],
)
def test_missing_items_count(global_state, state_fields, expected_missing):
    """missing_items_count: normal case, fully downloaded, negative protection."""
    for field, value in state_fields.items():
        setattr(global_state, field, value)
    assert global_state.missing_items_count() == expected_missing
