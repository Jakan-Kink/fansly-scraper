"""Integration tests for wall operations."""

import json
import os
from datetime import datetime, timezone
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
        """Set up test database and load test data."""
        # Create test database
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session: sessionmaker = sessionmaker(bind=cls.engine)

        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
        with open(os.path.join(cls.test_data_dir, "timeline-sample-account.json")) as f:
            cls.timeline_data = json.load(f)

    def setUp(self):
        """Set up fresh session and config for each test."""
        self.session: Session = self.Session()
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = ":memory:"
        self.config._database = Database(self.config)
        self.config._database.sync_engine = self.engine
        self.config._database.sync_session = self.Session

        # Create test account with unique ID
        self.account = Account(id=987654321, username="test_user")
        self.session.add(self.account)
        self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        self.session.close()

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
