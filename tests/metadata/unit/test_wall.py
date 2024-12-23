"""Unit tests for metadata.wall module."""

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from metadata.account import Account
from metadata.base import Base
from metadata.post import Post
from metadata.wall import Wall, process_account_walls, process_wall_posts


class TestWall(TestCase):
    """Test cases for Wall class and related functionality."""

    def setUp(self):
        """Set up test database and session."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Create test account
        self.account = Account(id=1, username="test_user")
        self.session.add(self.account)
        self.session.commit()

    def tearDown(self):
        """Clean up test database."""
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_wall_creation(self):
        """Test creating a wall with basic attributes."""
        wall = Wall(
            id=1, accountId=1, pos=1, name="Test Wall", description="Test Description"
        )
        self.session.add(wall)
        self.session.commit()

        saved_wall = self.session.query(Wall).first()
        self.assertEqual(saved_wall.name, "Test Wall")
        self.assertEqual(saved_wall.description, "Test Description")
        self.assertEqual(saved_wall.pos, 1)
        self.assertEqual(saved_wall.account, self.account)

    def test_wall_post_association(self):
        """Test associating posts with a wall."""
        # Create wall
        wall = Wall(id=1, accountId=1, name="Test Wall")
        self.session.add(wall)

        # Create posts
        posts = [
            Post(
                id=i,
                accountId=1,
                content=f"Post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            for i in range(1, 4)
        ]
        self.session.add_all(posts)
        self.session.flush()

        # Associate posts with wall
        wall.posts = posts
        self.session.commit()

        # Verify associations
        saved_wall = self.session.query(Wall).first()
        self.assertEqual(len(saved_wall.posts), 3)
        self.assertEqual(
            [p.content for p in saved_wall.posts], ["Post 1", "Post 2", "Post 3"]
        )

    def test_process_account_walls(self):
        """Test processing walls data for an account."""
        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.sync_session = self.Session
        walls_data = [
            {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
            {"id": 2, "pos": 2, "name": "Wall 2", "description": "Description 2"},
        ]

        process_account_walls(config_mock, self.account, walls_data)

        # Verify walls were created
        walls = self.session.query(Wall).order_by(Wall.pos).all()
        self.assertEqual(len(walls), 2)
        self.assertEqual(walls[0].name, "Wall 1")
        self.assertEqual(walls[1].name, "Wall 2")
        self.assertEqual(walls[0].pos, 1)
        self.assertEqual(walls[1].pos, 2)

    def test_wall_cleanup(self):
        """Test cleanup of removed walls."""
        # Create initial walls
        walls = [Wall(id=i, accountId=1, name=f"Wall {i}", pos=i) for i in range(1, 4)]
        self.session.add_all(walls)
        self.session.commit()

        # Process new walls data (missing one wall)
        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.sync_session = self.Session
        new_walls_data = [
            {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
            {"id": 3, "pos": 2, "name": "Wall 3", "description": "Description 3"},
        ]

        process_account_walls(config_mock, self.account, new_walls_data)

        # Verify wall 2 was removed
        remaining_walls = self.session.query(Wall).order_by(Wall.pos).all()
        self.assertEqual(len(remaining_walls), 2)
        self.assertEqual([w.id for w in remaining_walls], [1, 3])

    def test_process_wall_posts(self):
        """Test processing posts for a wall."""
        # Create wall
        wall = Wall(id=1, accountId=1, name="Test Wall")
        self.session.add(wall)
        self.session.commit()

        # Create posts data
        posts_data = {
            "posts": [
                {
                    "id": 1,
                    "accountId": 1,
                    "content": "Post 1",
                    "createdAt": int(datetime.now(timezone.utc).timestamp()),
                },
                {
                    "id": 2,
                    "accountId": 1,
                    "content": "Post 2",
                    "createdAt": int(datetime.now(timezone.utc).timestamp()),
                },
            ],
            "accounts": [{"id": 1, "username": "test_user"}],
            "accountMedia": [],
        }

        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.sync_session = self.Session
        process_wall_posts(config_mock, None, wall.id, posts_data)

        # Verify posts were associated with wall
        saved_wall = self.session.query(Wall).first()
        self.assertEqual(len(saved_wall.posts), 2)
        self.assertEqual(sorted(p.id for p in saved_wall.posts), [1, 2])
