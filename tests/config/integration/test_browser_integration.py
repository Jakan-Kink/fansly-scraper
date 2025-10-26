"""Integration tests for browser configuration utilities."""

import json
import os
import sqlite3
import tempfile
import unittest

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
        self.firefox_profile = os.path.join(self.temp_dir, "firefox", "profile")
        self.storage_dir = os.path.join(self.firefox_profile, "storage", "default")
        os.makedirs(self.storage_dir)

        # Create a test SQLite database
        self.db_path = os.path.join(self.storage_dir, "webappsstore.sqlite")
        self.create_test_sqlite_db()

    def tearDown(self):
        """Clean up temporary files."""
        try:
            import shutil

            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

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
        token = get_token_from_firefox_profile(self.firefox_profile)
        self.assertEqual(token, "test-fansly-token")

    def test_get_token_from_firefox_db_integration(self):
        """Test getting token directly from SQLite database."""
        token = get_token_from_firefox_db(self.db_path)
        self.assertEqual(token, "test-fansly-token")

    def test_find_leveldb_folders_integration(self):
        """Test finding LevelDB folders in a directory structure."""
        # Create some test leveldb folders
        leveldb_paths = [
            os.path.join(
                self.temp_dir, "browser1", "Default", "Local Storage", "leveldb"
            ),
            os.path.join(
                self.temp_dir, "browser2", "Profile 1", "Local Storage", "leveldb"
            ),
        ]

        # Create .ldb files in the folders
        for path in leveldb_paths:
            os.makedirs(path)
            with open(os.path.join(path, "000001.ldb"), "w") as f:
                f.write("test")

        # Create some non-leveldb folders
        os.makedirs(os.path.join(self.temp_dir, "browser3", "Other"))

        found_folders = find_leveldb_folders(self.temp_dir)

        self.assertEqual(len(found_folders), 2)
        for path in leveldb_paths:
            self.assertTrue(any(str(path) in str(found) for found in found_folders))
