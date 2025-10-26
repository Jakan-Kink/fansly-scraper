"""Tests for stash.types.job module."""

from datetime import datetime
from enum import Enum

import pytest
from strawberry import ID

from stash.types.job import (  # Enums; Types; Input types
    FindJobInput,
    Job,
    JobStatus,
    JobStatusUpdate,
    JobStatusUpdateType,
)


@pytest.mark.unit
class TestJobStatus:
    """Test JobStatus enum."""

    def test_strawberry_enum_decoration(self):
        """Test that JobStatus is decorated as strawberry enum."""
        assert issubclass(JobStatus, str)
        assert issubclass(JobStatus, Enum)

    def test_enum_values(self):
        """Test enum values."""
        assert JobStatus.READY == "READY"
        assert JobStatus.RUNNING == "RUNNING"  # type: ignore[unreachable]
        assert JobStatus.FINISHED == "FINISHED"
        assert JobStatus.STOPPING == "STOPPING"
        assert JobStatus.CANCELLED == "CANCELLED"
        assert JobStatus.FAILED == "FAILED"


@pytest.mark.unit
class TestJobStatusUpdateType:
    """Test JobStatusUpdateType enum."""

    def test_strawberry_enum_decoration(self):
        """Test that JobStatusUpdateType is decorated as strawberry enum."""
        assert issubclass(JobStatusUpdateType, str)
        assert issubclass(JobStatusUpdateType, Enum)

    def test_enum_values(self):
        """Test enum values."""
        assert JobStatusUpdateType.ADD == "ADD"
        assert JobStatusUpdateType.REMOVE == "REMOVE"  # type: ignore[unreachable]
        assert JobStatusUpdateType.UPDATE == "UPDATE"


@pytest.mark.unit
class TestJob:
    """Test Job class."""

    def test_strawberry_type_decoration(self):
        """Test that Job is decorated as strawberry type."""
        assert hasattr(Job, "__strawberry_definition__")
        assert not Job.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = Job.__annotations__
        assert annotations["id"] == ID
        assert annotations["status"] == JobStatus
        assert annotations["subTasks"] == list[str]
        assert annotations["description"] == str
        assert annotations["progress"] == float | None
        assert annotations["startTime"] == datetime | None
        assert annotations["endTime"] == datetime | None
        assert annotations["addTime"] == datetime
        assert annotations["error"] == str | None

    def test_required_fields(self):
        """Test that required fields are properly defined."""
        # Test instantiation with required fields
        now = datetime.now()
        job = Job(
            id=ID("1"),
            status=JobStatus.READY,
            subTasks=["task1", "task2"],
            description="Test job",
            addTime=now,
        )

        assert job.id == ID("1")
        assert job.status == JobStatus.READY
        assert job.subTasks == ["task1", "task2"]
        assert job.description == "Test job"
        assert job.addTime == now
        assert job.progress is None
        assert job.startTime is None
        assert job.endTime is None
        assert job.error is None

    def test_optional_fields(self):
        """Test optional fields can be set."""
        now = datetime.now()
        start_time = datetime.now()
        end_time = datetime.now()

        job = Job(
            id=ID("1"),
            status=JobStatus.FINISHED,
            subTasks=["task1"],
            description="Completed job",
            addTime=now,
            progress=100.0,
            startTime=start_time,
            endTime=end_time,
            error="No error",
        )

        assert job.progress == 100.0
        assert job.startTime == start_time
        assert job.endTime == end_time
        assert job.error == "No error"

    def test_job_status_transitions(self):
        """Test different job status values."""
        now = datetime.now()

        # Test each status
        for status in JobStatus:
            job = Job(
                id=ID("1"),
                status=status,
                subTasks=[],
                description=f"Job with status {status}",
                addTime=now,
            )
            assert job.status == status


@pytest.mark.unit
class TestJobStatusUpdate:
    """Test JobStatusUpdate class."""

    def test_strawberry_type_decoration(self):
        """Test that JobStatusUpdate is decorated as strawberry type."""
        assert hasattr(JobStatusUpdate, "__strawberry_definition__")
        assert not JobStatusUpdate.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = JobStatusUpdate.__annotations__
        assert annotations["type"] == JobStatusUpdateType
        assert annotations["job"] == Job

    def test_instantiation(self):
        """Test JobStatusUpdate can be instantiated."""
        job = Job(
            id=ID("1"),
            status=JobStatus.RUNNING,
            subTasks=["task1"],
            description="Running job",
            addTime=datetime.now(),
        )

        update = JobStatusUpdate(type=JobStatusUpdateType.UPDATE, job=job)

        assert update.type == JobStatusUpdateType.UPDATE
        assert update.job == job
        assert update.job.status == JobStatus.RUNNING

    def test_update_types(self):
        """Test different update types."""
        job = Job(
            id=ID("1"),
            status=JobStatus.READY,
            subTasks=[],
            description="Test job",
            addTime=datetime.now(),
        )

        # Test each update type
        for update_type in JobStatusUpdateType:
            update = JobStatusUpdate(type=update_type, job=job)
            assert update.type == update_type


@pytest.mark.unit
class TestFindJobInput:
    """Test FindJobInput class."""

    def test_strawberry_input_decoration(self):
        """Test that FindJobInput is decorated as strawberry input."""
        assert hasattr(FindJobInput, "__strawberry_definition__")
        assert FindJobInput.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = FindJobInput.__annotations__
        assert annotations["id"] == ID

    def test_instantiation(self):
        """Test FindJobInput can be instantiated."""
        find_input = FindJobInput(id=ID("123"))
        assert find_input.id == ID("123")


@pytest.mark.unit
class TestJobWorkflow:
    """Test job workflow scenarios."""

    def test_job_lifecycle(self):
        """Test a complete job lifecycle."""
        now = datetime.now()

        # Create job
        job = Job(
            id=ID("workflow-1"),
            status=JobStatus.READY,
            subTasks=["initialize", "process", "finalize"],
            description="Workflow test job",
            addTime=now,
        )

        # Job is ready
        assert job.status == JobStatus.READY
        assert job.startTime is None
        assert job.endTime is None
        assert job.progress is None

        # Start job
        job.status = JobStatus.RUNNING
        job.startTime = datetime.now()
        job.progress = 0.0

        assert job.status == JobStatus.RUNNING
        assert job.startTime is not None
        assert job.progress == 0.0

        # Update progress
        job.progress = 50.0
        assert job.progress == 50.0

        # Complete job
        job.status = JobStatus.FINISHED
        job.endTime = datetime.now()
        job.progress = 100.0

        assert job.status == JobStatus.FINISHED
        assert job.endTime is not None
        assert job.progress == 100.0

    def test_job_failure(self):
        """Test job failure scenario."""
        now = datetime.now()

        job = Job(
            id=ID("failure-1"),
            status=JobStatus.RUNNING,
            subTasks=["task1"],
            description="Failing job",
            addTime=now,
            startTime=now,
        )

        # Job fails
        job.status = JobStatus.FAILED
        job.endTime = datetime.now()
        job.error = "Task failed with error"

        assert job.status == JobStatus.FAILED
        assert job.error == "Task failed with error"
        assert job.endTime is not None

    def test_job_cancellation(self):
        """Test job cancellation scenario."""
        now = datetime.now()

        job = Job(
            id=ID("cancel-1"),
            status=JobStatus.RUNNING,
            subTasks=["long-task"],
            description="Cancellable job",
            addTime=now,
            startTime=now,
            progress=25.0,
        )

        # Job is being stopped
        job.status = JobStatus.STOPPING
        assert job.status == JobStatus.STOPPING

        # Job is cancelled
        job.status = JobStatus.CANCELLED
        job.endTime = datetime.now()

        assert job.status == JobStatus.CANCELLED
        assert job.endTime is not None
        assert job.progress == 25.0  # Progress preserved at cancellation point
