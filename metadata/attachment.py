from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeVar

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, func, select
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import set_committed_value

from config.decorators import with_database_session
from textio import json_output

from .base import Base
from .story import Story


if TYPE_CHECKING:
    from .account import AccountMedia, AccountMediaBundle
    from .messages import Message
    from .post import Post


class HasAttachments(Protocol):
    """Protocol for models that can have attachments."""

    id: int
    attachments: list[Attachment]


T = TypeVar("T", bound=HasAttachments)


class ContentType(Enum):
    """Content types for attachments.

    Defines the possible types of content that can be attached to posts or messages.
    Each type corresponds to a specific kind of content in the system.

    Attributes:
        ACCOUNT_MEDIA: Individual media item (type=1)
        ACCOUNT_MEDIA_BUNDLE: Collection of media items (type=2)
        AGGREGATED_POSTS: Array of aggregated post dictionaries (type=8)
        TIP_GOALS: Tip goal content (type=7100)
        STORY: Story content (type=32001)
        POLL: Poll content (type=42001)
    """

    ACCOUNT_MEDIA = 1
    ACCOUNT_MEDIA_BUNDLE = 2
    AGGREGATED_POSTS = 8
    TIP_GOALS = 7100
    STORY = 32001
    POLL = 42001


class Attachment(Base):
    """Represents an attachment to a post or message.

    This class handles attachments to posts and messages, maintaining their order
    and content type. Each attachment can reference different types of content
    (media, bundles, etc.) and is ordered within its parent through the pos field.

    Attributes:
        id: Unique identifier for the attachment
        postId: ID of the post this attachment belongs to (if any)
        messageId: ID of the message this attachment belongs to (if any)
        contentId: ID of the referenced content
        pos: Position/order of this attachment within its parent
        contentType: Type of the referenced content (from ContentType enum)
        post: Relationship to the parent Post (if any)
        message: Relationship to the parent Message (if any)
        media: Relationship to AccountMedia (if contentType is ACCOUNT_MEDIA)
        bundle: Relationship to AccountMediaBundle (if contentType is ACCOUNT_MEDIA_BUNDLE)

    Note:
        An attachment can only belong to either a post or a message, not both.
        This is enforced by the check_post_or_message_exclusivity constraint.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postId: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("posts.id"), nullable=True
    )
    messageId: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id"), nullable=True
    )
    contentId: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    contentType: Mapped[ContentType] = mapped_column(
        SQLEnum(ContentType), nullable=False
    )
    post: Mapped[Post | None] = relationship("Post", back_populates="attachments")
    message: Mapped[Message | None] = relationship(
        "Message", back_populates="attachments"
    )

    # Polymorphic relationships based on contentType
    media: Mapped[AccountMedia | None] = relationship(
        "AccountMedia",
        primaryjoin="and_(Attachment.contentType == 'ACCOUNT_MEDIA', "
        "Attachment.contentId == AccountMedia.id)",
        foreign_keys=[contentId],
        viewonly=True,
    )
    bundle: Mapped[AccountMediaBundle | None] = relationship(
        "AccountMediaBundle",
        primaryjoin="and_(Attachment.contentType == 'ACCOUNT_MEDIA_BUNDLE', "
        "Attachment.contentId == AccountMediaBundle.id)",
        foreign_keys=[contentId],
        viewonly=True,
    )
    aggregated_post: Mapped[Post | None] = relationship(
        "Post",
        primaryjoin="and_(Attachment.contentType == 'AGGREGATED_POSTS', "
        "Attachment.contentId == Post.id)",
        foreign_keys=[contentId],
        viewonly=True,
    )
    story: Mapped[Story | None] = relationship(
        "Story",
        primaryjoin="and_(Attachment.contentType == 'STORY', "
        "Attachment.contentId == Story.id)",
        foreign_keys=[contentId],
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            '("postId" IS NULL OR "messageId" IS NULL)',  # Either postId or messageId must be NULL
            name="check_post_or_message_exclusivity",
        ),
    )

    async def resolve_content(
        self,
    ) -> AccountMedia | AccountMediaBundle | Post | Story | None:
        """Resolve the content based on contentType and contentId.

        Returns:
            The related AccountMedia, AccountMediaBundle, Post, or Story object, or None
        """
        # Since we're using viewonly relationships, we can use them directly
        # even in async context as they don't trigger lazy loads
        if self.contentType == ContentType.ACCOUNT_MEDIA:
            return self.media
        if self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE:
            return self.bundle
        if self.contentType == ContentType.AGGREGATED_POSTS:
            return self.aggregated_post
        if self.contentType == ContentType.STORY:
            return self.story
        return None

    @property
    def is_account_media(self) -> bool:
        """Return True if the contentType indicates AccountMedia."""
        return self.contentType == ContentType.ACCOUNT_MEDIA

    @property
    def is_account_media_bundle(self) -> bool:
        """Return True if the contentType indicates AccountMediaBundle."""
        return self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE

    @property
    def is_aggregated_post(self) -> bool:
        """Return True if the contentType indicates an aggregated post."""
        return self.contentType == ContentType.AGGREGATED_POSTS

    @property
    def is_story(self) -> bool:
        """Return True if the contentType indicates a story."""
        return self.contentType == ContentType.STORY

    @with_database_session(async_session=True)
    async def process_story(
        self, story_data: dict, session: AsyncSession | None = None
    ) -> Story:
        """Process a story attachment from API data.

        This method creates or updates a Story object based on the provided API data.
        It handles all the necessary database operations to ensure the story exists
        and is up to date.

        Args:
            session: AsyncSession for database operations
            story_data: Dictionary containing story data from the API with fields:
                - id: Story ID
                - authorId: ID of the account that authored this story
                - title: Story title (optional)
                - description: Story description (optional)
                - content: Main story content
                - createdAt: Creation timestamp
                - updatedAt: Last update timestamp (optional)

        Returns:
            Story: The created or updated Story object

        Note:
            The story_data dictionary should contain at least id, authorId, content,
            and createdAt fields. Other fields are optional.

        Raises:
            ValueError: If this attachment is not a story type
            KeyError: If required fields are missing from story_data
        """
        if not self.is_story:
            raise ValueError("Cannot process story for non-story attachment")

        # Ensure required fields are present
        required_fields = {"id", "authorId", "content", "createdAt"}
        missing_fields = required_fields - story_data.keys()
        if missing_fields:
            raise KeyError(f"Missing required fields for story: {missing_fields}")

        # Query for existing story
        result = await session.execute(
            select(Story).where(Story.id == story_data["id"])
        )
        story = result.scalar_one_or_none()

        # Create new story if it doesn't exist
        if story is None:
            story = Story(
                id=story_data["id"],
                authorId=story_data["authorId"],
                content=story_data["content"],
                createdAt=story_data["createdAt"],
            )
            session.add(story)

        # Update optional fields if they exist in the data
        for field in ["title", "description", "updatedAt"]:
            if field in story_data:
                set_committed_value(story, field, story_data[field])

        return story

    @classmethod
    @with_database_session(async_session=True)
    async def process_attachment(
        cls,
        attachment_data: dict[str, any],
        parent: T,
        known_relations: set[str],
        parent_field: str,
        session: AsyncSession | None = None,
        context: str = "attachment",
    ) -> None:
        """Process a single attachment for a parent model.

        Args:
            session: SQLAlchemy async session
            attachment_data: Dictionary containing attachment data
            parent: Parent model instance (Message or Post)
            known_relations: Set of known relationship fields
            parent_field: Field name linking attachment to parent (e.g., "messageId" or "postId")
            context: Context string for logging
        """
        # Convert contentType to enum first
        try:
            attachment_data["contentType"] = ContentType(attachment_data["contentType"])
        except ValueError:
            old_content_type = attachment_data["contentType"]
            attachment_data["contentType"] = None
            json_output(
                2,
                f"meta/{context} - invalid_content_type: {old_content_type}",
                attachment_data,
            )

        # Process attachment data
        filtered_attachment, _ = cls.process_data(
            attachment_data,
            known_relations,
            f"meta/{context}",
        )

        # Ensure required fields are present
        if "contentId" not in filtered_attachment:
            json_output(
                1,
                f"meta/{context} - missing_required_field",
                {
                    parent_field: parent.id,
                    "missing_field": "contentId",
                },
            )
            return

        # Set parent ID after filtering
        filtered_attachment[parent_field] = parent.id

        # Get or create attachment
        attachment, created = await cls.async_get_or_create(
            session,
            {
                parent_field: parent.id,
                "contentId": filtered_attachment["contentId"],
            },
        )

        # Set position if not provided for new attachments
        if created and "pos" not in filtered_attachment:
            result = await session.execute(
                select(func.count())  # pylint: disable=not-callable
                .select_from(cls)
                .where(getattr(cls, parent_field) == parent.id)
            )
            max_pos = result.scalar_one()
            filtered_attachment["pos"] = max_pos + 1

        # Update fields
        Base.update_fields(attachment, filtered_attachment)
        await session.flush()
