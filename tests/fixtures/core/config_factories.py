"""FactoryBoy factories for FanslyConfig and related configuration objects.

This module provides factories for creating test instances of configuration objects
using FactoryBoy. These factories create real configuration objects with sensible defaults,
replacing the need for MagicMock usage in tests.

Usage:
    from tests.fixtures.core import FanslyConfigFactory

    # Create a basic config for testing
    config = FanslyConfigFactory()

    # Create a config with specific values
    config = FanslyConfigFactory(
        token="custom_token",
        download_mode=DownloadMode.TIMELINE
    )

    # Create a config with database (uses real database fixture)
    config = FanslyConfigFactory(
        pg_database="test_db_123",
        pg_host="localhost"
    )
"""

import os
from pathlib import Path

from factory import Factory, LazyAttribute, LazyFunction

from config import FanslyConfig
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode


class FanslyConfigFactory(Factory):
    """Factory for FanslyConfig instances.

    Creates FanslyConfig instances with realistic defaults.
    Override any fields when creating instances.

    Example:
        # Basic config for unit tests
        config = FanslyConfigFactory()

        # Config with API credentials
        config = FanslyConfigFactory(
            token="test_auth_token_123456789",
            user_agent="Mozilla/5.0 Test"
        )

        # Config with download settings
        config = FanslyConfigFactory(
            download_mode=DownloadMode.MESSAGES,
            download_path=Path("/tmp/test_downloads")
        )

    Note:
        By default, this factory creates configs that do NOT connect to real databases.
        The pg_database field is set to a non-existent name to prevent accidental
        connections. Use the database fixtures (uuid_test_db_factory, config) for
        tests that need real database access.
    """

    class Meta:
        model = FanslyConfig

    # Required field
    program_version = "0.11.0-test"

    # File-independent fields
    use_following = False
    reverse_order = False
    DUPLICATE_THRESHOLD = 50
    BATCH_SIZE = 150
    config_path = None
    token_from_browser_name = None
    debug = False
    trace = False
    post_id = None
    updated_to = None

    # Private fields (None by default)
    _api = None
    _database = None
    _base = None
    _stash = None
    _background_tasks = LazyFunction(list)

    # Account credentials (None by default - set explicitly in tests)
    token = None
    user_agent = None
    check_key = None

    # Targeted creator
    user_names = None

    # Download settings
    download_mode = DownloadMode.NOTSET
    download_path = LazyFunction(lambda: Path("/tmp/test_downloads"))  # noqa: S108
    local_directory = LazyAttribute(lambda o: str(o.download_path))

    # Metadata handling
    metadata_handling = MetadataHandling.ADVANCED
    metadata_db_file = None  # Default to PostgreSQL

    # PostgreSQL settings - default to safe non-existent database
    # This prevents accidental connections to production/test databases
    pg_host = LazyFunction(lambda: os.getenv("FANSLY_PG_HOST", "localhost"))
    pg_port = LazyFunction(lambda: int(os.getenv("FANSLY_PG_PORT", "5432")))
    pg_user = LazyFunction(
        lambda: os.getenv("FANSLY_PG_USER", os.getenv("USER", "postgres"))
    )
    pg_password = LazyFunction(lambda: os.getenv("FANSLY_PG_PASSWORD", ""))
    pg_database = "test_mock_should_not_connect"  # Safe default

    # Database sync settings
    db_sync_min_size = 50
    db_sync_commits = 1000
    db_sync_seconds = 60

    # UI/UX settings
    interactive = False  # Non-interactive by default for tests
    textio_debug = False
    show_downloads = False
    show_skipped = False

    # Stash integration
    stash_enabled = False
    stash_url = None
    stash_api_key = None

    # Download filtering
    download_media = True
    download_previews = False
    show_metadata = False

    # File management
    update_times = True
    separate_messages = False
    separate_previews = False

    # Advanced options
    dedupe_model = None
    quality = None
    quality_str = None


__all__ = [
    "FanslyConfigFactory",
]
