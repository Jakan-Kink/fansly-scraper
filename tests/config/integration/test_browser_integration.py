"""Integration tests for browser configuration utilities."""

import contextlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from config.browser import (
    find_leveldb_folders,
    get_token_from_firefox_db,
    get_token_from_firefox_profile,
)


class TestBrowserIntegration(unittest.TestCase):
    """Integration tests for browser-related functionality."""

    def setUp(self):
        """Set up temporary test directory structure."""
        self.temp_dir = tempfile.mkdtemp()
        self.firefox_profile = Path(self.temp_dir) / "firefox" / "profile"
        self.storage_dir = self.firefox_profile / "storage" / "default"
        self.storage_dir.mkdir(parents=True)

        # Create a test SQLite database
        self.db_path = self.storage_dir / "webappsstore.sqlite"
        self.create_test_sqlite_db()

    def tearDown(self):
        """Clean up temporary files."""
        with contextlib.suppress(Exception):
            shutil.rmtree(self.temp_dir)

    def create_test_sqlite_db(self):
        """Create a test SQLite database with a token."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create a table similar to Firefox's storage
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS webappsstore2 (
                key TEXT,
                value BLOB,
                utf16 INTEGER,
                compressed INTEGER,
                usage INTEGER,
                data BLOB
            )
        """
        )

        # Insert test token
        test_data = json.dumps({"token": "test-fansly-token"}).encode("utf-8")
        cursor.execute(
            "INSERT INTO webappsstore2 VALUES (?, ?, ?, ?, ?, ?)",
            ("session_active_session", None, 0, 0, 0, test_data),
        )

        conn.commit()
        conn.close()

    def test_get_token_from_firefox_profile_integration(self):
        """Test getting token from a real Firefox profile structure."""
        token = get_token_from_firefox_profile(str(self.firefox_profile))
        assert token == "test-fansly-token"

    def test_get_token_from_firefox_db_integration(self):
        """Test getting token directly from SQLite database."""
        token = get_token_from_firefox_db(str(self.db_path))
        assert token == "test-fansly-token"

    def test_find_leveldb_folders_integration(self):
        """Test finding LevelDB folders in a directory structure."""
        # Create some test leveldb folders
        leveldb_paths = [
            Path(self.temp_dir) / "browser1" / "Default" / "Local Storage" / "leveldb",
            Path(self.temp_dir)
            / "browser2"
            / "Profile 1"
            / "Local Storage"
            / "leveldb",
        ]

        # Create .ldb files in the folders
        for path in leveldb_paths:
            path.mkdir(parents=True)
            (path / "000001.ldb").write_text("test")

        # Create some non-leveldb folders
        (Path(self.temp_dir) / "browser3" / "Other").mkdir(parents=True)

        found_folders = find_leveldb_folders(self.temp_dir)

        assert len(found_folders) == 2
        for path in leveldb_paths:
            assert any(str(path) in str(found) for found in found_folders)
