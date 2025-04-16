import pytest

from download.globalstate import GlobalState


@pytest.fixture
def global_state():
    """Create a test global state."""
    return GlobalState()


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


def test_missing_items_count_normal(global_state):
    """Test missing items calculation in normal case."""
    global_state.total_timeline_pictures = 10
    global_state.total_timeline_videos = 5
    global_state.total_message_items = 3
    global_state.pic_count = 8
    global_state.vid_count = 4
    global_state.duplicate_count = 2
    assert global_state.missing_items_count() == 4


def test_missing_items_count_zero(global_state):
    """Test missing items calculation when everything is downloaded."""
    global_state.total_timeline_pictures = 5
    global_state.total_timeline_videos = 5
    global_state.pic_count = 5
    global_state.vid_count = 5
    assert global_state.missing_items_count() == 0


def test_missing_items_count_negative_protection(global_state):
    """Test missing items stays at 0 when calculation would be negative."""
    global_state.pic_count = 10
    global_state.vid_count = 5
    assert global_state.missing_items_count() == 0
