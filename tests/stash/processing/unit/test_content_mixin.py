"""Tests for the ContentProcessingMixin.

This module imports all the content mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Import modules instead of classes to avoid fixture issues
import tests.stash.processing.unit.content.test_message_processing
import tests.stash.processing.unit.content.test_post_processing
from metadata import Account, Message, Post
from stash.processing.mixins.content import ContentProcessingMixin
from stash.types import Performer, Studio


class TestMixinClass(ContentProcessingMixin):
    """Test class that implements ContentProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._process_item_gallery = AsyncMock()
        self._setup_batch_processing = AsyncMock()
        self._run_batch_processor = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for ContentProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def mock_session():
    """Fixture for mock session."""
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def mock_studio():
    """Fixture for mock studio."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    return studio


class TestContentProcessingWithRealData:
    """Test content processing mixin with real JSON data."""

    @pytest.mark.asyncio
    async def test_process_posts_with_real_data(
        self,
        mixin,
        mock_session,
        sample_post,
        sample_account,
        mock_performer,
        mock_studio,
    ):
        """Test processing posts with real data from JSON."""
        # Mock session to return our sample post and account
        mock_session.execute.return_value.scalar_one.return_value = sample_account
        mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [
            sample_post
        ]

        # Mock batch processing functions
        mixin._setup_batch_processing.return_value = (
            MagicMock(),  # task_pbar
            MagicMock(),  # process_pbar
            MagicMock(),  # semaphore
            MagicMock(),  # queue
        )

        # Call process_creator_posts with real data
        await mixin.process_creator_posts(
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Verify session operations
        mock_session.add.assert_called_with(sample_account)
        mock_session.execute.assert_called()

        # Verify batch processing setup
        mixin._setup_batch_processing.assert_called_once()
        assert "post" in str(mixin._setup_batch_processing.call_args)

        # Verify batch processor execution
        mixin._run_batch_processor.assert_called_once()
        batch_args = mixin._run_batch_processor.call_args[1]
        assert "items" in batch_args
        assert batch_args["items"] == [sample_post]
        assert batch_args["batch_size"] == 25
        assert "process_batch" in batch_args

    @pytest.mark.asyncio
    async def test_process_messages_with_real_data(
        self,
        mixin,
        mock_session,
        sample_message,
        sample_account,
        mock_performer,
        mock_studio,
    ):
        """Test processing messages with real data from JSON."""
        # Mock session to return our sample message and account
        mock_session.execute.return_value.scalar_one.return_value = sample_account
        mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [
            sample_message
        ]

        # Mock batch processing functions
        mixin._setup_batch_processing.return_value = (
            MagicMock(),  # task_pbar
            MagicMock(),  # process_pbar
            MagicMock(),  # semaphore
            MagicMock(),  # queue
        )

        # Call process_creator_messages with real data
        await mixin.process_creator_messages(
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Verify session operations
        mock_session.add.assert_called_with(sample_account)
        mock_session.execute.assert_called()

        # Verify batch processing setup
        mixin._setup_batch_processing.assert_called_once()
        assert "message" in str(mixin._setup_batch_processing.call_args)

        # Verify batch processor execution
        mixin._run_batch_processor.assert_called_once()
        batch_args = mixin._run_batch_processor.call_args[1]
        assert "items" in batch_args
        assert batch_args["items"] == [sample_message]
        assert batch_args["batch_size"] == 25
        assert "process_batch" in batch_args

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_real_data(
        self,
        mixin,
        mock_session,
        sample_post,
        sample_account,
        mock_performer,
        mock_studio,
    ):
        """Test _process_items_with_gallery with real data from JSON."""

        # Define URL pattern function
        def get_post_url(post):
            return f"https://fansly.com/post/{post.id}"

        # Call _process_items_with_gallery with real data
        await mixin._process_items_with_gallery(
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            items=[sample_post],
            url_pattern_func=get_post_url,
            session=mock_session,
        )

        # Verify _process_item_gallery was called with correct args
        mixin._process_item_gallery.assert_called_once()
        call_args = mixin._process_item_gallery.call_args
        assert call_args[1]["item"] == sample_post
        assert call_args[1]["account"] == sample_account
        assert call_args[1]["performer"] == mock_performer
        assert call_args[1]["studio"] == mock_studio
        assert call_args[1]["item_type"] == "post"
        assert (
            call_args[1]["url_pattern"] == f"https://fansly.com/post/{sample_post.id}"
        )
        assert call_args[1]["session"] == mock_session


# No need to import classes directly as they're discovered by pytest
