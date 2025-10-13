"""
Unit tests for the Scheduler service.

These tests verify scheduling logic, time parsing, and task management.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from dateutil import tz

from rp.core.models import TaskStatus
from rp.core.scheduler import Scheduler
from rp.utils.errors import SchedulingError


class TestScheduler:
    """Test Scheduler service."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler instance for testing."""
        with patch("rp.core.scheduler.SCHEDULE_FILE") as mock_file:
            mock_file.exists.return_value = False
            mock_file.open.side_effect = FileNotFoundError()
            scheduler = Scheduler()
            # Explicitly set empty tasks to ensure clean state
            scheduler._tasks = []
            return scheduler

    def test_parse_time_string_hhmm_today(self, scheduler):
        """Test parsing HH:MM format for today."""
        now = datetime(2022, 1, 20, 10, 0, tzinfo=tz.tzlocal())

        # Time in the future today
        result = scheduler.parse_time_string("15:30", now)
        expected = now.replace(hour=15, minute=30, second=0, microsecond=0)
        assert result == expected

    def test_parse_time_string_hhmm_tomorrow(self, scheduler):
        """Test parsing HH:MM format rolls to tomorrow if past."""
        now = datetime(2022, 1, 20, 16, 0, tzinfo=tz.tzlocal())

        # Time in the past today should roll to tomorrow
        result = scheduler.parse_time_string("15:30", now)
        expected = (now + timedelta(days=1)).replace(
            hour=15, minute=30, second=0, microsecond=0
        )
        assert result == expected

    def test_parse_time_string_tomorrow_explicit(self, scheduler):
        """Test parsing 'tomorrow HH:MM' format."""
        now = datetime(2022, 1, 20, 10, 0, tzinfo=tz.tzlocal())

        result = scheduler.parse_time_string("tomorrow 15:30", now)
        expected = (now + timedelta(days=1)).replace(
            hour=15, minute=30, second=0, microsecond=0
        )
        assert result == expected

    def test_parse_time_string_explicit_datetime(self, scheduler):
        """Test parsing explicit datetime formats."""
        result = scheduler.parse_time_string("2022-01-25 14:30")
        assert result.year == 2022
        assert result.month == 1
        assert result.day == 25
        assert result.hour == 14
        assert result.minute == 30

    def test_parse_time_string_invalid(self, scheduler):
        """Test parsing invalid time strings."""
        with pytest.raises(SchedulingError):
            scheduler.parse_time_string("")

        with pytest.raises(SchedulingError):
            scheduler.parse_time_string("invalid")

        with pytest.raises(SchedulingError):
            scheduler.parse_time_string("25:99")  # invalid time

    def test_parse_duration_string(self, scheduler):
        """Test parsing duration strings."""
        assert scheduler.parse_duration_string("30m") == 1800
        assert scheduler.parse_duration_string("2h") == 7200
        assert scheduler.parse_duration_string("1d") == 86400
        assert scheduler.parse_duration_string("1h30m") == 5400
        assert scheduler.parse_duration_string("2d3h45m30s") == 186330

    def test_parse_duration_string_invalid(self, scheduler):
        """Test parsing invalid duration strings."""
        with pytest.raises(SchedulingError):
            scheduler.parse_duration_string("")

        with pytest.raises(SchedulingError):
            scheduler.parse_duration_string("invalid")

        with pytest.raises(SchedulingError):
            scheduler.parse_duration_string("0m")

    def test_schedule_stop(self, scheduler):
        """Test scheduling a stop task."""
        when = datetime(2022, 1, 25, 15, 30, tzinfo=tz.tzlocal())

        with patch.object(scheduler, "_save_tasks"):
            task = scheduler.schedule_stop("test-pod", when)

        assert task.action == "stop"
        assert task.alias == "test-pod"
        assert task.status == TaskStatus.PENDING
        assert task.when_epoch == int(when.timestamp())
        assert len(scheduler.tasks) == 1

    def test_cancel_task(self, scheduler):
        """Test cancelling a task."""
        when = datetime(2022, 1, 25, 15, 30, tzinfo=tz.tzlocal())

        with patch.object(scheduler, "_save_tasks"):
            task = scheduler.schedule_stop("test-pod", when)
            cancelled_task = scheduler.cancel_task(task.id)

        assert cancelled_task.status == TaskStatus.CANCELLED

    def test_cancel_already_completed_task(self, scheduler):
        """Test cancelling an already completed task."""
        when = datetime(2022, 1, 25, 15, 30, tzinfo=tz.tzlocal())

        with patch.object(scheduler, "_save_tasks"):
            task = scheduler.schedule_stop("test-pod", when)
            task.status = TaskStatus.COMPLETED

            # Should return the task unchanged
            cancelled_task = scheduler.cancel_task(task.id)
            assert cancelled_task.status == TaskStatus.COMPLETED

    def test_get_due_tasks(self, scheduler):
        """Test getting due tasks."""
        past_time = datetime(2022, 1, 20, 10, 0, tzinfo=tz.tzlocal())
        future_time = datetime(2022, 1, 25, 15, 30, tzinfo=tz.tzlocal())
        current_epoch = int(
            datetime(2022, 1, 22, 12, 0, tzinfo=tz.tzlocal()).timestamp()
        )

        with patch.object(scheduler, "_save_tasks"):
            past_task = scheduler.schedule_stop("past-pod", past_time)
            scheduler.schedule_stop("future-pod", future_time)

        due_tasks = scheduler.get_due_tasks(current_epoch)

        assert len(due_tasks) == 1
        assert due_tasks[0].id == past_task.id

    def test_clean_completed_tasks(self, scheduler):
        """Test cleaning completed and cancelled tasks."""
        when = datetime(2022, 1, 25, 15, 30, tzinfo=tz.tzlocal())

        with patch.object(scheduler, "_save_tasks"):
            # Create tasks with different statuses
            task1 = scheduler.schedule_stop("pod1", when)
            task2 = scheduler.schedule_stop("pod2", when)
            task3 = scheduler.schedule_stop("pod3", when)
            task4 = scheduler.schedule_stop("pod4", when)

            task1.status = TaskStatus.COMPLETED
            task2.status = TaskStatus.CANCELLED
            task3.status = TaskStatus.PENDING
            task4.status = TaskStatus.FAILED

            removed = scheduler.clean_completed_tasks()

        assert removed == 2  # Two completed/cancelled tasks removed
        assert len(scheduler.tasks) == 2  # Two active tasks remain (pending, failed)
        assert all(
            task.status in {TaskStatus.PENDING, TaskStatus.FAILED}
            for task in scheduler.tasks
        )

    def test_task_not_found(self, scheduler):
        """Test error when task not found."""
        with pytest.raises(SchedulingError):
            scheduler.get_task("nonexistent-id")
