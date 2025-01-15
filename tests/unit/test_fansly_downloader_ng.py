"""Unit tests for fansly_downloader_ng module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy.sql import text

from config import FanslyConfig
from config.modes import DownloadMode
from download.core import get_creator_account_info
from errors import (
    API_ERROR,
    CONFIG_ERROR,
    DOWNLOAD_ERROR,
    EXIT_ABORT,
    EXIT_SUCCESS,
    SOME_USERS_FAILED,
    UNEXPECTED_ERROR,
    ApiAccountInfoError,
    ApiError,
    ConfigError,
    DownloadError,
)
from fansly_downloader_ng import cleanup_database, main, print_logo
from metadata.account import process_account_data


def test_print_logo(capsys):
    """Test print_logo function outputs correctly."""
    print_logo()
    captured = capsys.readouterr()
    # The logo is ASCII art, so we check for key parts
    assert "███████╗" in captured.out  # Part of the F
    assert "github.com/prof79/fansly-downloader-ng" in captured.out


def test_cleanup_database_success():
    """Test cleanup_database with successful database close."""
    config = MagicMock()
    mock_db = MagicMock()
    config._database = mock_db

    cleanup_database(config)

    mock_db.close.assert_called_once()


def test_cleanup_database_error(capsys):
    """Test cleanup_database handling database close error."""
    config = MagicMock()
    mock_db = MagicMock()
    mock_db.close.side_effect = Exception("Database error")
    config._database = mock_db

    cleanup_database(config)

    mock_db.close.assert_called_once()
    captured = capsys.readouterr()
    assert "Error closing database connections: Database error" in captured.out


def test_cleanup_database_no_database():
    """Test cleanup_database when no database exists."""
    config = MagicMock()
    config._database = None

    cleanup_database(config)  # Should not raise any exception


@pytest.fixture
def mock_args():
    """Fixture to create mocked command line arguments."""
    args = MagicMock()
    args.normal = True
    args.messages = False
    args.timeline = False
    args.collection = False
    args.single = None
    args.users = ["test_user"]
    args.download_directory = None
    args.authorization_token = None
    args.user_agent = None
    args.check_key = None
    args.metadata_handling = "simple"
    args.metadata_db_file = "test.db"
    args.download_mode_single = None
    args.download_mode_collection = None
    args.download_mode_messages = None
    args.download_mode_timeline = None
    args.download_mode_wall = None
    args.download_mode_normal = True
    return args


@pytest.fixture
def mock_config():
    """Fixture to create a mocked FanslyConfig."""
    config = MagicMock(spec=FanslyConfig)
    config.program_version = "0.10.0"
    config.config_path = Path("config.ini")
    config.user_names = {"test_user"}
    config.download_mode = DownloadMode.NORMAL
    config.separate_metadata = False
    config.metadata_db_file = "test.db"
    config.interactive = False  # Disable interactive mode for tests
    # Set direct values for retries and other config
    config.timeline_retries = 3
    config.messages_retries = 3
    config.wall_retries = 3
    config.collection_retries = 3
    config.single_retries = 3
    config.check_key = "test_key"
    config.timeline_delay_seconds = 5
    config.use_duplicate_threshold = False
    config.show_downloads = True
    config.show_skipped_downloads = True
    config.debug = False
    config.interactive = False
    config.fetchedTimelineDuplication = False
    config.creator_id = "test_creator_id"

    # Mock retries as properties
    type(config).timeline_retries = PropertyMock(return_value=3)
    type(config).messages_retries = PropertyMock(return_value=3)
    type(config).wall_retries = PropertyMock(return_value=3)
    type(config).collection_retries = PropertyMock(return_value=3)
    type(config).single_retries = PropertyMock(return_value=3)

    # Mock API
    mock_api = MagicMock()
    mock_api.get_client_user_name.return_value = "client_user"
    mock_api.get_group.return_value = MagicMock(status_code=404, text="Not found")
    mock_api.get_timeline.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "response": {"posts": [], "accountMedia": [], "accounts": [], "media": []}
        },
    )
    mock_api.get_wall.return_value = MagicMock(status_code=404, text="Not found")
    config.get_api.return_value = mock_api
    config.get_background_tasks.return_value = []

    # Mock parser
    mock_parser = MagicMock()

    def mock_get(section, option, fallback=None):
        value = {
            ("Downloader", "download_mode"): "NORMAL",
            ("Downloader", "metadata_handling"): "simple",
            ("API", "token"): "test_token",
            ("API", "check_key"): "test_key",
            ("API", "device_id"): "test_device_id",
            ("API", "session_id"): "test_session_id",
            ("Downloader", "timeline_retries"): "3",
            ("Downloader", "messages_retries"): "3",
            ("Downloader", "wall_retries"): "3",
            ("Downloader", "collection_retries"): "3",
            ("Downloader", "single_retries"): "3",
        }.get((section, option))
        return value if value is not None else fallback

    mock_parser.get.side_effect = mock_get
    config._parser = mock_parser

    return config


@pytest.fixture
def mock_database():
    """Fixture to mock Database class."""
    with patch("metadata.database.Database") as mock:
        # Mock SQLAlchemy engine and dialect
        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_dialect = MagicMock()
        mock_dialect.name = "sqlite"
        mock_connection.dialect = mock_dialect
        mock_engine.connect.return_value.__enter__.return_value = mock_connection
        mock_instance = mock.return_value
        mock_instance.sync_engine = mock_engine
        mock_instance.db_file = Path("test.db")
        mock_instance.close = MagicMock()  # Add close method
        mock_instance._optimized_connection = MagicMock()  # Add _optimized_connection
        mock_instance._optimized_connection.close = MagicMock()  # Add close method
        mock_instance._optimized_connection.is_closed = False  # Add is_closed flag
        mock_instance._optimized_connection.close.side_effect = lambda: setattr(
            mock_instance._optimized_connection, "is_closed", True
        )  # Set is_closed on close

        # Mock database schema
        def mock_execute(statement, *args, **kwargs):
            result = MagicMock()
            if "sqlite_master" in str(statement):
                # Return None for first check (table doesn't exist)
                # Return a value for subsequent checks (table exists)
                result.fetchone.return_value = (
                    None
                    if not hasattr(mock_execute, "called")
                    else ("alembic_version",)
                )
                setattr(mock_execute, "called", True)
            elif "alembic_version" in str(statement):
                # Return None for first check (no version)
                # Return a version for subsequent checks
                result.scalar.return_value = (
                    None
                    if not hasattr(mock_execute, "version_called")
                    else "1c766f50e19a"
                )
                setattr(mock_execute, "version_called", True)
            elif "integrity_check" in str(statement):
                result.fetchall.return_value = [("ok",)]
            elif "quick_check" in str(statement):
                result.fetchall.return_value = [("ok",)]
            return result

        mock_connection.execute = mock_execute
        mock_connection.commit = MagicMock()

        yield mock


@pytest.fixture
def mock_alembic():
    """Fixture to mock Alembic configuration."""
    with (
        patch("alembic.config.Config") as mock_config,
        patch("alembic.command.upgrade") as mock_upgrade,
        patch("alembic.script.base.ScriptDirectory") as mock_script,
    ):
        # Mock Alembic config
        mock_instance = MagicMock()
        mock_instance.get_main_option.return_value = "os"  # Valid separator value
        mock_config.return_value = mock_instance

        # Mock upgrade command to create tables
        def mock_upgrade_impl(config, revision, **kw):
            connection = config.attributes["connection"]
            # Create all necessary tables
            connection.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME,
                    recipientId INTEGER,
                    text TEXT
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME,
                    displayName TEXT,
                    about TEXT,
                    location TEXT,
                    profilePictureUrl TEXT,
                    bannerImageUrl TEXT,
                    followersCount INTEGER,
                    followingCount INTEGER,
                    postsCount INTEGER,
                    mediaCount INTEGER,
                    listsCount INTEGER,
                    favoritedCount INTEGER,
                    lastSeenAt DATETIME,
                    joinedAt DATETIME,
                    isVerified BOOLEAN,
                    isBlocked BOOLEAN,
                    isBlockedBy BOOLEAN,
                    isFollowing BOOLEAN,
                    isFollowed BOOLEAN,
                    isFavorited BOOLEAN,
                    isSubscribed BOOLEAN,
                    subscriptionPrice FLOAT,
                    subscriptionTiersCount INTEGER,
                    subscriptionBundlesCount INTEGER,
                    subscriptionGoals TEXT,
                    subscriptionGoalsProgress TEXT,
                    subscriptionGoalsCompleted TEXT
                );

                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME,
                    hash TEXT,
                    local_filename TEXT,
                    is_downloaded BOOLEAN DEFAULT FALSE,
                    mimetype TEXT,
                    size INTEGER,
                    width INTEGER,
                    height INTEGER,
                    duration INTEGER,
                    variants TEXT,
                    preview TEXT,
                    thumbnail TEXT,
                    isProcessed BOOLEAN,
                    isPublic BOOLEAN,
                    isDeleted BOOLEAN
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY,
                    content TEXT,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME,
                    accountId INTEGER,
                    price FLOAT,
                    isArchived BOOLEAN,
                    isDeleted BOOLEAN,
                    isHidden BOOLEAN,
                    isPinned BOOLEAN,
                    isPublic BOOLEAN,
                    isSubscriberOnly BOOLEAN,
                    likesCount INTEGER,
                    commentsCount INTEGER,
                    tipsCount INTEGER,
                    shareCount INTEGER,
                    FOREIGN KEY(accountId) REFERENCES accounts(id)
                );

                CREATE TABLE IF NOT EXISTS timeline_posts (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER,
                    media_id INTEGER,
                    FOREIGN KEY(post_id) REFERENCES posts(id),
                    FOREIGN KEY(media_id) REFERENCES media(id)
                );

                CREATE TABLE IF NOT EXISTS wall_posts (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER,
                    media_id INTEGER,
                    FOREIGN KEY(post_id) REFERENCES posts(id),
                    FOREIGN KEY(media_id) REFERENCES media(id)
                );

                CREATE TABLE IF NOT EXISTS message_media (
                    id INTEGER PRIMARY KEY,
                    message_id INTEGER,
                    media_id INTEGER,
                    FOREIGN KEY(message_id) REFERENCES messages(id),
                    FOREIGN KEY(media_id) REFERENCES media(id)
                );

                CREATE TABLE IF NOT EXISTS hashtags (
                    id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL UNIQUE,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME
                );

                CREATE TABLE IF NOT EXISTS post_hashtags (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER,
                    hashtag_id INTEGER,
                    FOREIGN KEY(post_id) REFERENCES posts(id),
                    FOREIGN KEY(hashtag_id) REFERENCES hashtags(id)
                );

                CREATE TABLE IF NOT EXISTS post_mentions (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER,
                    account_id INTEGER,
                    FOREIGN KEY(post_id) REFERENCES posts(id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                );

                CREATE TABLE IF NOT EXISTS message_mentions (
                    id INTEGER PRIMARY KEY,
                    message_id INTEGER,
                    account_id INTEGER,
                    FOREIGN KEY(message_id) REFERENCES messages(id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    id INTEGER PRIMARY KEY,
                    follower_id INTEGER,
                    followed_id INTEGER,
                    createdAt DATETIME,
                    updatedAt DATETIME,
                    deletedAt DATETIME,
                    FOREIGN KEY(follower_id) REFERENCES accounts(id),
                    FOREIGN KEY(followed_id) REFERENCES accounts(id)
                );
            """
                )
            )
            connection.commit()

        mock_upgrade.side_effect = mock_upgrade_impl

        # Mock script directory
        mock_script_instance = MagicMock()
        mock_script.from_config.return_value = mock_script_instance
        mock_script_instance.get_current_head.return_value = "head"

        yield mock_config


@pytest.fixture
def mock_download_functions():
    """Fixture to mock all download-related functions."""
    with (
        patch("download.single.download_single_post") as mock_single,
        patch("download.collections.download_collections") as mock_collections,
        patch("download.messages.download_messages") as mock_messages,
        patch("download.timeline.download_timeline") as mock_timeline,
        patch("download.wall.download_wall") as mock_wall,
    ):
        yield {
            "single": mock_single,
            "collections": mock_collections,
            "messages": mock_messages,
            "timeline": mock_timeline,
            "wall": mock_wall,
        }


@pytest.fixture
def mock_state():
    """Mock state fixture."""
    with patch("download.globalstate.GlobalState") as mock_state_class:
        # Create a real GlobalState instance
        state = MagicMock()
        state.duplicate_count = 0
        state.pic_count = 0
        state.vid_count = 0
        state.total_message_items = 0
        state.total_timeline_pictures = 0
        state.total_timeline_videos = 0
        state.total_timeline_items = MagicMock(return_value=0)
        state.total_downloaded_items = MagicMock(return_value=0)
        state.missing_items_count = MagicMock(
            side_effect=lambda: 0
        )  # Use side_effect to return a value
        state.total_items_count = MagicMock(side_effect=lambda: 0)
        state.downloaded_items_count = MagicMock(side_effect=lambda: 0)
        state.skipped_items_count = MagicMock(side_effect=lambda: 0)
        state.failed_items_count = MagicMock(side_effect=lambda: 0)
        mock_state_class.return_value = state
        yield state


@pytest.fixture
def mock_common_functions(tmp_path):
    """Fixture to mock common functions."""
    # Create necessary directories
    creator_dir = tmp_path / "test_user_fansly"
    creator_dir.mkdir(parents=True)
    meta_dir = creator_dir / "meta"
    meta_dir.mkdir()

    patches = [
        patch("metadata.account.process_account_data"),
        patch("download.core.get_creator_account_info"),
        patch("fileio.dedupe.dedupe_init"),
        patch("download.statistics.print_statistics"),
        patch("download.statistics.print_global_statistics"),
        patch("download.statistics.print_timing_statistics"),
        patch("helpers.common.open_location"),
        patch("builtins.input", return_value="y"),  # Mock input to always return "y"
        patch("pathio.pathio.get_creator_base_path", return_value=creator_dir),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def test_main_success(
    mock_config,
    mock_args,
    mock_database,
    mock_alembic,
    mock_download_functions,
    mock_common_functions,
    mock_state,
):
    """Test main function with successful execution."""
    with (
        patch("config.load_config"),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("config.args.map_args_to_config"),
        patch("updater.self_update"),
        patch("config.validation.validate_adjust_config"),
    ):
        result = main(mock_config)
        assert result == EXIT_SUCCESS


def test_main_api_account_error(
    mock_config, mock_args, mock_database, mock_alembic, mock_common_functions
):
    """Test main function handling ApiAccountInfoError."""
    mock_config.get_api.return_value.get_creator_account_info.side_effect = (
        ApiAccountInfoError("API error")
    )

    with (
        patch("config.load_config"),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("config.args.map_args_to_config"),
        patch("updater.self_update"),
        patch("config.validation.validate_adjust_config"),
        patch("textio.textio.input_enter_continue"),
    ):
        result = main(mock_config)
        assert result == SOME_USERS_FAILED


def test_main_with_background_tasks(
    mock_config, mock_args, mock_database, mock_alembic, mock_common_functions
):
    """Test main function with background tasks."""
    mock_task = AsyncMock()
    mock_config.get_background_tasks.return_value = [mock_task]

    # Mock asyncio event loop
    mock_loop = MagicMock()
    mock_loop.run_until_complete = MagicMock()
    mock_loop.create_task = MagicMock()

    with (
        patch("config.load_config"),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("config.args.map_args_to_config"),
        patch("updater.self_update"),
        patch("config.validation.validate_adjust_config"),
        patch("asyncio.new_event_loop", return_value=mock_loop),
        patch("asyncio.set_event_loop"),
        patch("asyncio.gather", new_callable=AsyncMock),
    ):
        result = main(mock_config)
        assert result == EXIT_SUCCESS
        # Verify the task was added to the event loop
        assert mock_loop.create_task.called


def test_main_keyboard_interrupt(mock_config, mock_args, mock_common_functions):
    """Test main function handling KeyboardInterrupt."""
    with (
        patch("config.load_config"),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("config.args.map_args_to_config"),
        patch("updater.self_update"),
        patch("config.validation.validate_adjust_config"),
        patch("textio.textio.input_enter_close"),
    ):
        with pytest.raises(KeyboardInterrupt):
            main(mock_config)


@pytest.mark.parametrize(
    "error,expected_code",
    [
        (ApiError("API error"), API_ERROR),
        (ConfigError("Config error"), CONFIG_ERROR),
        (DownloadError("Download error"), DOWNLOAD_ERROR),
        (Exception("Unexpected error"), UNEXPECTED_ERROR),
    ],
)
def test_main_error_handling(
    mock_config, mock_args, mock_common_functions, error, expected_code
):
    """Test main function handling various errors."""
    # Set up the mock to raise the error at the right point
    if isinstance(error, ApiError):
        mock_config.get_api.return_value.get_creator_account_info.side_effect = error
    elif isinstance(error, ConfigError):
        mock_config._parser.get.side_effect = error
    elif isinstance(error, DownloadError):
        mock_config.get_api.return_value.get_creator_account_info.side_effect = error
    else:
        mock_config.get_api.return_value.get_creator_account_info.side_effect = error

    with (
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("textio.textio.input_enter_close"),
    ):
        with pytest.raises(type(error)):
            main(mock_config)


def test_main_cleanup_on_exit(
    mock_config, mock_args, mock_database, mock_alembic, mock_common_functions
):
    """Test main function cleanup on exit."""
    mock_config.get_background_tasks.return_value = [AsyncMock()]

    # Register cleanup function
    atexit_funcs = []

    def mock_register(func, *args):
        atexit_funcs.append((func, args))

    with (
        patch("atexit.register", side_effect=mock_register),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        patch("config.args.map_args_to_config"),
        patch("updater.self_update"),
        patch("config.validation.validate_adjust_config"),
        patch("textio.textio.input_enter_close"),
        patch("asyncio.get_event_loop") as mock_get_loop,
    ):
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop

        # Create a mock database instance
        mock_db = MagicMock()
        mock_db.close = MagicMock()  # Explicitly mock close method
        mock_config._database = mock_db

        # Trigger an error to test cleanup
        mock_config.get_api.return_value.get_creator_account_info.side_effect = (
            Exception("Test error")
        )

        # Run main and expect it to raise the test error
        with pytest.raises(Exception):
            main(mock_config)

        # Call registered cleanup functions
        for func, args in atexit_funcs:
            func(*args)

        # Verify cleanup was called
        assert (
            mock_db.close.call_count == 1
        ), f"Expected close to be called once. Called {mock_db.close.call_count} times."
        mock_loop.stop.assert_called_once()
        mock_loop.close.assert_called_once()
