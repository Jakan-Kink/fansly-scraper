"""Unit tests for relationship logger functionality."""

from datetime import datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from metadata.relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    missing_relationships,
    print_missing_relationships_summary,
)


# Remove global mark and add it to individual async tests


@pytest.mark.asyncio
async def test_log_missing_relationship_existing(session):
    """Test logging when relationship exists."""
    # Create test data
    await session.execute(
        text("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY)")
    )
    await session.execute(text("INSERT INTO test_table (id) VALUES (1)"))
    await session.commit()

    # Clear any existing relationships
    clear_missing_relationships()

    # Test logging existing relationship
    exists = await log_missing_relationship(
        session=session,
        table_name="referencing_table",
        field_name="test_field",
        missing_id=1,
        referenced_table="test_table",
    )

    assert exists is True
    assert not missing_relationships  # Should be empty since relationship exists


@pytest.mark.asyncio
async def test_log_missing_relationship_nonexistent(session):
    """Test logging when relationship doesn't exist."""
    # Create test table
    await session.execute(
        text("CREATE TABLE IF NOT EXISTS nonexistent_table (id INTEGER PRIMARY KEY)")
    )
    await session.commit()

    # Clear any existing relationships
    clear_missing_relationships()

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        exists = await log_missing_relationship(
            session=session,
            table_name="referencing_table",
            field_name="test_field",
            missing_id=999,
            referenced_table="nonexistent_table",
            context={"extra": "info"},
        )

        assert exists is False
        assert "nonexistent_table" in missing_relationships
        assert "referencing_table" in missing_relationships["nonexistent_table"]
        assert "999" in missing_relationships["nonexistent_table"]["referencing_table"]

        # Verify json output was called with correct data
        mock_json_output.assert_called_once()
        args = mock_json_output.call_args[0]
        assert args[0] == 1  # Log level
        assert (
            args[1] == "meta/relationships/missing/nonexistent_table/referencing_table"
        )
        log_data = args[2]
        assert log_data["table"] == "referencing_table"
        assert log_data["field"] == "test_field"
        assert log_data["missing_id"] == "999"
        assert log_data["referenced_table"] == "nonexistent_table"
        assert log_data["extra"] == "info"
        assert isinstance(datetime.fromisoformat(log_data["timestamp"]), datetime)


def test_print_missing_relationships_summary_empty():
    """Test printing summary when no missing relationships."""
    clear_missing_relationships()
    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        print_missing_relationships_summary()

        mock_json_output.assert_called_once_with(
            1,
            "meta/relationships/summary",
            {"message": "No missing relationships found"},
        )


def test_print_missing_relationships_summary_with_data():
    """Test printing summary with missing relationships."""
    clear_missing_relationships()

    # Add some test data
    missing_relationships["table1"]["ref1"].add("1")
    missing_relationships["table1"]["ref1"].add("2")
    missing_relationships["table1"]["ref2"].add("3")
    missing_relationships["table2"]["ref3"].add("4")

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        print_missing_relationships_summary()

        mock_json_output.assert_called_once()
        args = mock_json_output.call_args[0]
        assert args[0] == 1
        assert args[1] == "meta/relationships/summary"
        summary = args[2]

        # Check missing_relationships structure
        assert "table1" in summary["missing_relationships"]
        assert "table2" in summary["missing_relationships"]
        assert set(summary["missing_relationships"]["table1"]["ref1"]) == {"1", "2"}
        assert set(summary["missing_relationships"]["table1"]["ref2"]) == {"3"}
        assert set(summary["missing_relationships"]["table2"]["ref3"]) == {"4"}

        # Check counts structure
        assert summary["counts"]["table1"]["ref1"] == 2
        assert summary["counts"]["table1"]["ref2"] == 1
        assert summary["counts"]["table2"]["ref3"] == 1


def test_clear_missing_relationships():
    """Test clearing missing relationships tracking."""
    # Add some test data
    missing_relationships["table1"]["ref1"].add("1")
    missing_relationships["table2"]["ref2"].add("2")

    assert len(missing_relationships) > 0
    clear_missing_relationships()
    assert len(missing_relationships) == 0


@pytest.mark.asyncio
async def test_log_missing_relationship_with_none_id(session):
    """Test logging with None ID."""
    # Create test table
    await session.execute(
        text("CREATE TABLE IF NOT EXISTS ref_table (id INTEGER PRIMARY KEY)")
    )
    await session.commit()

    exists = await log_missing_relationship(
        session=session,
        table_name="test_table",
        field_name="test_field",
        missing_id=None,
        referenced_table="ref_table",
    )

    assert exists is False
    assert "ref_table" in missing_relationships
    assert "test_table" in missing_relationships["ref_table"]
    assert "None" in missing_relationships["ref_table"]["test_table"]


@pytest_asyncio.fixture
async def session(test_engine):
    """Get test session."""
    # Create session factory
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create session
    async with async_session_factory() as session:
        # PostgreSQL: No PRAGMA statements needed
        yield session

        # Clean up - roll back any pending transactions
        await session.rollback()


@pytest.mark.asyncio
async def test_log_missing_relationship_multiple_times(session):
    """Test logging same missing relationship multiple times."""
    # Create test table
    await session.execute(
        text("CREATE TABLE IF NOT EXISTS ref_table (id INTEGER PRIMARY KEY)")
    )
    await session.commit()

    # Clear any existing relationships
    clear_missing_relationships()

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        # Log same relationship twice
        for _ in range(2):
            await log_missing_relationship(
                session=session,
                table_name="test_table",
                field_name="test_field",
                missing_id=1,
                referenced_table="ref_table",
            )

        # Should be logged in set only once
        assert len(missing_relationships["ref_table"]["test_table"]) == 1
        assert (
            mock_json_output.call_count == 1
        )  # Should be output only once for the same relationship


@pytest.mark.parametrize(
    "missing_id",
    [
        123,  # Integer
        "456",  # String
        123.45,  # Float
        True,  # Boolean
    ],
)
@pytest.mark.asyncio
async def test_log_missing_relationship_id_types(session, missing_id):
    """Test logging with different ID types."""
    # Create test table
    await session.execute(
        text("CREATE TABLE IF NOT EXISTS ref_table (id INTEGER PRIMARY KEY)")
    )
    await session.commit()

    exists = await log_missing_relationship(
        session=session,
        table_name="test_table",
        field_name="test_field",
        missing_id=missing_id,
        referenced_table="ref_table",
    )

    assert exists is False
    assert str(missing_id) in missing_relationships["ref_table"]["test_table"]
