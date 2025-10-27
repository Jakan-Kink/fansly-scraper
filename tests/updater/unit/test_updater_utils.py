import platform

from updater import utils


def test_parse_release_notes_success():
    # Given a release_info with a code block containing release notes
    release_info = {"body": "Header text\n```\nRelease note content\n```\nFooter text"}
    result = utils.parse_release_notes(release_info)
    assert result.strip() == "Release note content"


def test_parse_release_notes_no_body():
    release_info = {}
    result = utils.parse_release_notes(release_info)
    assert result is None


def test_parse_release_notes_no_match():
    release_info = {"body": "No code block here"}
    result = utils.parse_release_notes(release_info)
    assert result is None


def test_display_release_notes(monkeypatch):
    # Monkey-patch input to bypass waiting on enter
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    # Monkey-patch clear_terminal to do nothing
    monkeypatch.setattr(utils, "clear_terminal", lambda: None)

    printed_messages = []

    def fake_print_update(msg):
        printed_messages.append(msg)

    monkeypatch.setattr(utils, "print_update", fake_print_update)

    program_version = "1.0.0"
    release_notes = "New features here."
    utils.display_release_notes(program_version, release_notes)

    # Check that the printed update message contains expected information
    combined = " ".join(printed_messages)
    assert "Successfully updated to version 1.0.0" in combined
    assert "New features here" in combined


def test_perform_update_invalid_release_info(monkeypatch):
    program_version = "1.0.0"
    # Missing required keys
    release_info = {}
    result = utils.perform_update(program_version, release_info)
    assert result is False


def test_perform_update_valid(monkeypatch):
    program_version = "1.0.0"
    # Valid release_info with required keys
    release_info = {
        "release_version": "1.1.0",
        "created_at": "2025-01-01T00:00:00Z",
        "download_count": 100,
        "browser_download_url": "http://example.com/download",
    }
    monkeypatch.setattr(utils, "print_warning", lambda _msg: None)
    monkeypatch.setattr(utils, "print_info", lambda _msg: None)
    result = utils.perform_update(program_version, release_info)
    # Due to early return in perform_update, result is always False
    assert result is False


def test_check_for_update_no_release_info(monkeypatch):
    class DummyConfig:
        program_version = "1.0.0"

    config = DummyConfig()
    monkeypatch.setattr(utils, "get_release_info_from_github", lambda _version: None)
    result = utils.check_for_update(config)
    assert result is False


def test_check_for_update_draft(monkeypatch):
    class DummyConfig:
        program_version = "1.0.0"

    config = DummyConfig()
    release_info = {"draft": True, "prerelease": False}
    monkeypatch.setattr(
        utils, "get_release_info_from_github", lambda _version: release_info
    )
    result = utils.check_for_update(config)
    assert result is False


def test_check_for_update_up_to_date(monkeypatch):
    class DummyConfig:
        program_version = "1.1.0"

    config = DummyConfig()
    asset = {
        "name": f"{platform.system()}_build",
        "created_at": "2025-01-01T00:00:00Z",
        "download_count": 50,
        "browser_download_url": "http://example.com/download",
    }
    release_info = {
        "draft": False,
        "prerelease": False,
        "tag_name": "v1.0.0",
        "assets": [asset],
    }
    monkeypatch.setattr(
        utils, "get_release_info_from_github", lambda _version: release_info
    )
    result = utils.check_for_update(config)
    # Since current version 1.1.0 is >= new version 1.0.0, returns True
    assert result is True


def test_check_for_update_outdated(monkeypatch):
    class DummyConfig:
        program_version = "1.0.0"

    config = DummyConfig()
    asset = {
        "name": f"{platform.system()}_build",
        "created_at": "2025-01-01T00:00:00Z",
        "download_count": 50,
        "browser_download_url": "http://example.com/download",
    }
    release_info = {
        "draft": False,
        "prerelease": False,
        "tag_name": "v1.1.0",
        "assets": [asset],
    }
    monkeypatch.setattr(
        utils, "get_release_info_from_github", lambda _version: release_info
    )
    # Force perform_update to return False
    monkeypatch.setattr(utils, "perform_update", lambda _current, _new_release: False)
    result = utils.check_for_update(config)
    assert result is False
