"""Unit tests for metadata/attachable.py"""

from metadata.attachable import Attachable


class TestAttachable:
    """Tests for Attachable base class."""

    def test_attachable_tablename_property(self):
        """Test that __tablename__ property returns class name in lowercase."""

        # Create a concrete subclass for testing
        class TestAttachableSubclass(Attachable):
            __abstract__ = False
            __mapper_args__ = {
                "polymorphic_identity": "test_subclass",
            }

        # Access the __tablename__ through the class descriptor
        assert TestAttachableSubclass.__tablename__ == "testattachablesubclass"

    def test_attachable_is_abstract(self):
        """Test that Attachable is marked as abstract."""
        assert Attachable.__abstract__ is True

    def test_attachable_mapper_args(self):
        """Test that Attachable has correct mapper args."""
        assert "polymorphic_identity" in Attachable.__mapper_args__
        assert "polymorphic_on" in Attachable.__mapper_args__
        assert Attachable.__mapper_args__["polymorphic_identity"] == "attachable"
