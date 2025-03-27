"""Test models for unit tests."""

from sqlalchemy import Integer, MetaData, String
from sqlalchemy.orm import Mapped, mapped_column

from metadata.base import Base

# Create a separate metadata for testing
test_metadata = MetaData()


class SampleModel(Base):
    """Test model for verifying Base functionality."""

    __tablename__ = "test_models"
    metadata = test_metadata  # Use test metadata to avoid conflicts

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    async def async_method(self) -> str:
        """Test async method."""
        return f"Async {self.name}"

    def sync_method(self) -> str:
        """Test sync method."""
        return f"Sync {self.name}"
