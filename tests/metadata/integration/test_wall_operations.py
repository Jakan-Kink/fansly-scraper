"""Integration tests for wall operations."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import FanslyConfig
from metadata.account import Account
from metadata.base import Base
from metadata.database import Database
from metadata.post import Post
from metadata.wall import Wall, process_account_walls, process_wall_posts


class TestWallOperations(TestCase):
    """Integration tests for wall operations."""

    @classmethod
    def setUpClass(cls):
        """Load test data."""
        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
        with open(os.path.join(cls.test_data_dir, "timeline-sample-account.json")) as f:
            cls.timeline_data = json.load(f)

    def setUp(self):
        """Set up fresh database and session for each test."""
        # Create test database
        self.engine = create_engine("sqlite:///:memory:")
        # Base.metadata.create_all(self.engine)
        self.Session: sessionmaker = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Create config with test database
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = Path(":memory:")
        self.config._database = Database(self.config)
        self.config._database._sync_engine = self.engine
        self.config._database.session_scope = self.Session

        # Generate unique ID based on test name
        test_name = self._testMethodName
        import hashlib

        unique_id = (
            int(
                hashlib.sha1(
                    f"{self.__class__.__name__}_{test_name}".encode()
                ).hexdigest()[:8],
                16,
            )
            % 1000000
        )

        # Create test account with unique ID
        self.account = Account(id=unique_id, username=f"test_user_{unique_id}")
        self.session.add(self.account)
        self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        try:
            # Clean up data
            for table in reversed(Base.metadata.sorted_tables):
                self.session.execute(table.delete())
            self.session.commit()
        except Exception:
            self.session.rollback()
        finally:
            self.session.close()
            self.engine.dispose()

    def test_wall_post_integration(self):
        """Test full wall and post integration."""
        # Create walls
        walls = [
            Wall(
                id=i,
                accountId=self.account.id,
                name=f"Wall {i}",
                pos=i,
                description=f"Description {i}",
            )
            for i in range(1, 3)
        ]
        self.session.add_all(walls)

        # Create posts
        posts = [
            Post(
                id=i,
                accountId=self.account.id,
                content=f"Post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            for i in range(1, 5)
        ]
        self.session.add_all(posts)
        self.session.commit()

        # Associate posts with walls
        walls[0].posts = posts[:2]  # First two posts to first wall
        walls[1].posts = posts[2:]  # Last two posts to second wall
        self.session.commit()

        # Verify through separate session
        with self.Session() as verify_session:
            # Check first wall
            wall1 = verify_session.query(Wall).get(1)
            self.assertEqual(len(wall1.posts), 2)
            self.assertEqual(
                sorted(p.content for p in wall1.posts), ["Post 1", "Post 2"]
            )

            # Check second wall
            wall2 = verify_session.query(Wall).get(2)
            self.assertEqual(len(wall2.posts), 2)
            self.assertEqual(
                sorted(p.content for p in wall2.posts), ["Post 3", "Post 4"]
            )

    def test_wall_updates_with_posts(self):
        """Test updating walls while maintaining post associations."""
        # Create initial wall with posts
        wall = Wall(id=1, accountId=self.account.id, name="Original Wall", pos=1)
        self.session.add(wall)

        posts = [
            Post(
                id=i,
                accountId=self.account.id,
                content=f"Post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            for i in range(1, 3)
        ]
        self.session.add_all(posts)
        wall.posts = posts
        self.session.commit()

        # Update wall through process_account_walls
        new_wall_data = [
            {
                "id": 1,
                "pos": 2,  # Changed position
                "name": "Updated Wall",  # Changed name
                "description": "New description",
            }
        ]

        process_account_walls(self.config, self.account, new_wall_data)

        # Verify updates in new session
        with self.Session() as verify_session:
            updated_wall = verify_session.query(Wall).get(1)
            self.assertEqual(updated_wall.name, "Updated Wall")
            self.assertEqual(updated_wall.pos, 2)
            self.assertEqual(updated_wall.description, "New description")

            # Verify posts are still associated
            self.assertEqual(len(updated_wall.posts), 2)
            self.assertEqual(
                sorted(p.content for p in updated_wall.posts), ["Post 1", "Post 2"]
            )

    def test_wall_post_processing(self):
        """Test processing wall posts from timeline-style data."""
        # Create wall
        wall = Wall(id=1, accountId=self.account.id, name="Test Wall")
        self.session.add(wall)
        self.session.commit()

        # Create posts data in timeline format
        posts_data = {
            "posts": [
                {
                    "id": i,
                    "accountId": self.account.id,
                    "content": f"Post {i}",
                    "createdAt": int(datetime.now(timezone.utc).timestamp()),
                }
                for i in range(1, 4)
            ],
            "accounts": [{"id": self.account.id, "username": self.account.username}],
            "accountMedia": [],  # Empty list to avoid KeyError
        }

        # Process posts
        process_wall_posts(self.config, None, wall.id, posts_data)

        # Verify in new session
        with self.Session() as verify_session:
            wall = verify_session.query(Wall).get(1)
            self.assertEqual(len(wall.posts), 3)

            # Verify post content
            post_contents = sorted(p.content for p in wall.posts)
            self.assertEqual(post_contents, ["Post 1", "Post 2", "Post 3"])

            # Verify post-wall relationships
            for post in wall.posts:
                self.assertIn(wall, post.walls)
