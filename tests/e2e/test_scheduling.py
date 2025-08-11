"""
End-to-end tests for scheduling functionality.

Tests the scheduling system with real pods but minimal time delays.
"""

from datetime import datetime, timedelta

from dateutil import tz


class TestScheduling:
    """Test pod scheduling functionality."""

    def test_schedule_stop_and_cancel(self, cli_runner, shared_test_pod):
        """Test scheduling a pod stop and then cancelling it."""
        alias = "schedule-test"
        pod_id = shared_test_pod["pod_id"]

        # Add pod to alias system
        result = cli_runner(["add", alias, pod_id])
        assert result.returncode == 0

        # Schedule stop in 5 minutes (far enough to cancel)
        result = cli_runner(["stop", alias, "--schedule-in", "5m"])
        assert result.returncode == 0
        assert "Scheduled stop" in result.stdout

        # Extract task ID from output
        task_id = None
        for line in result.stdout.split("\n"):
            if "id=" in line:
                start = line.find("id=") + 3
                end = line.find(")", start)
                if end == -1:
                    end = len(line)
                task_id = line[start:end].strip()
                break

        assert task_id, "Could not extract task ID from schedule output"

        # List scheduled tasks
        result = cli_runner(["schedule", "list"])
        assert result.returncode == 0
        assert task_id in result.stdout
        assert alias in result.stdout
        assert "pending" in result.stdout.lower()

        # Cancel the scheduled task
        result = cli_runner(["schedule", "cancel", task_id])
        assert result.returncode == 0
        assert "Cancelled task" in result.stdout

        # Verify it's cancelled
        result = cli_runner(["schedule", "list"])
        assert result.returncode == 0
        if task_id in result.stdout:  # Task might still be listed
            assert "cancelled" in result.stdout.lower()

        # Clean up
        result = cli_runner(["delete", alias])
        assert result.returncode == 0

    def test_schedule_at_time_format(self, cli_runner, shared_test_pod):
        """Test different time format options for scheduling."""
        alias = "schedule-time-test"
        pod_id = shared_test_pod["pod_id"]

        # Add pod to alias system
        result = cli_runner(["add", alias, pod_id])
        assert result.returncode == 0

        # Test scheduling at specific time (tomorrow to avoid immediate execution)
        tomorrow = datetime.now(tz.tzlocal()) + timedelta(days=1)
        time_str = tomorrow.strftime("%H:%M")

        result = cli_runner(["stop", alias, "--schedule-at", f"tomorrow {time_str}"])
        assert result.returncode == 0
        assert "Scheduled stop" in result.stdout

        # List to verify it's there
        result = cli_runner(["schedule", "list"])
        assert result.returncode == 0
        assert alias in result.stdout

        # Clean up - clear completed tasks to remove our test tasks
        result = cli_runner(["schedule", "clear-completed"])
        assert result.returncode == 0

        # Clean up alias
        result = cli_runner(["delete", alias])
        assert result.returncode == 0

    def test_dry_run_scheduling(self, cli_runner, shared_test_pod):
        """Test dry-run mode for scheduling."""
        alias = "dry-run-test"
        pod_id = shared_test_pod["pod_id"]

        # Add pod to alias system
        result = cli_runner(["add", alias, pod_id])
        assert result.returncode == 0

        # Test dry-run immediate stop
        result = cli_runner(["stop", alias, "--dry-run"])
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        assert "Would stop" in result.stdout

        # Test dry-run scheduled stop
        result = cli_runner(["stop", alias, "--schedule-in", "1h", "--dry-run"])
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout
        assert "Would schedule stop" in result.stdout

        # Verify no actual tasks were created
        result = cli_runner(["schedule", "list"])
        assert result.returncode == 0
        # Should be empty or not contain our alias
        assert alias not in result.stdout or "No scheduled tasks" in result.stdout

        # Clean up
        result = cli_runner(["delete", alias])
        assert result.returncode == 0

    def test_invalid_schedule_formats(self, cli_runner, shared_test_pod):
        """Test error handling for invalid schedule formats."""
        alias = "invalid-schedule-test"
        pod_id = shared_test_pod["pod_id"]

        # Add pod to alias system
        result = cli_runner(["add", alias, pod_id])
        assert result.returncode == 0

        # Test invalid duration format
        result = cli_runner(["stop", alias, "--schedule-in", "invalid"])
        assert result.returncode != 0
        assert "Invalid" in result.stderr

        # Test invalid time format
        result = cli_runner(["stop", alias, "--schedule-at", "25:99"])
        assert result.returncode != 0

        # Test conflicting options
        result = cli_runner(
            ["stop", alias, "--schedule-at", "tomorrow 10:00", "--schedule-in", "1h"]
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr

        # Clean up
        result = cli_runner(["delete", alias])
        assert result.returncode == 0
