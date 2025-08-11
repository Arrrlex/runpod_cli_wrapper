"""
Task scheduling service for the RunPod CLI wrapper.

This module provides functionality for scheduling pod operations to run at
specific times or after delays, with persistent storage and execution tracking.
"""

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser as date_parser
from dateutil import tz
from rich.console import Console

from runpod_cli_wrapper.config import (
    API_KEY_FILE,
    LAUNCH_AGENTS_DIR,
    LAUNCHD_LABEL,
    LAUNCHD_PLIST,
    LOGS_DIR,
    SCHEDULE_FILE,
    SCHEDULER_LOG_FILE,
    ensure_config_dir_exists,
)
from runpod_cli_wrapper.core.models import ScheduleTask, TaskStatus
from runpod_cli_wrapper.utils.errors import SchedulingError


class Scheduler:
    """Service for managing scheduled tasks."""

    def __init__(self):
        """Initialize the scheduler."""
        self._tasks: list[ScheduleTask] | None = None

    @property
    def tasks(self) -> list[ScheduleTask]:
        """Get current scheduled tasks, loading from disk if needed."""
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def _load_tasks(self) -> list[ScheduleTask]:
        """Load scheduled tasks from storage."""
        try:
            with SCHEDULE_FILE.open("r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [ScheduleTask.model_validate(item) for item in data]
                return []
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return []

    def _save_tasks(self) -> None:
        """Save scheduled tasks to storage."""
        ensure_config_dir_exists()
        tmp_path = SCHEDULE_FILE.with_suffix(".json.tmp")

        task_dicts = [task.model_dump() for task in self.tasks]
        with tmp_path.open("w") as f:
            json.dump(task_dicts, f, indent=2, sort_keys=True)
            f.write("\n")

        tmp_path.replace(SCHEDULE_FILE)

    def clear_completed_tasks(self) -> int:
        """Remove completed tasks and return count removed."""
        original_count = len(self.tasks)
        self._tasks = [t for t in self.tasks if t.status != TaskStatus.COMPLETED]

        removed = original_count - len(self.tasks)
        if removed > 0:
            self._save_tasks()

        return removed

    def parse_time_string(self, time_str: str, now: datetime | None = None) -> datetime:
        """Parse a time string into an aware datetime."""
        if not time_str or not time_str.strip():
            raise SchedulingError.invalid_time_format(time_str, "Empty time string")

        text = time_str.strip()
        local_tz = tz.tzlocal()
        now = now or datetime.now(local_tz)

        # Handle "tomorrow HH:MM"
        match = re.match(r"^tomorrow\s+(\d{1,2}):(\d{2})$", text, re.IGNORECASE)
        if match:
            try:
                hour = int(match.group(1))
                minute = int(match.group(2))
                target = (now + timedelta(days=1)).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                return target
            except ValueError as e:
                raise SchedulingError.invalid_time_format(time_str, str(e)) from e

        # Handle "HH:MM" (today or tomorrow if past)
        match = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if match:
            try:
                hour = int(match.group(1))
                minute = int(match.group(2))
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target = target + timedelta(days=1)
                return target
            except ValueError as e:
                raise SchedulingError.invalid_time_format(time_str, str(e)) from e

        # Try explicit datetime formats
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.replace(tzinfo=local_tz)
            except ValueError:
                continue

        # Fallback to dateutil parser
        try:
            dt = date_parser.parse(
                text,
                default=now.replace(hour=0, minute=0, second=0, microsecond=0),
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            return dt
        except Exception as e:
            raise SchedulingError.invalid_time_format(time_str, str(e)) from e

    def parse_duration_string(self, duration_str: str) -> int:
        """Parse a duration string into seconds."""
        if not duration_str or not duration_str.strip():
            raise SchedulingError.invalid_time_format(
                duration_str, "Empty duration string"
            )

        duration_re = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
        multipliers = {"d": 86400, "h": 3600, "m": 60, "s": 1}

        total = 0
        for match in duration_re.finditer(duration_str.strip()):
            try:
                value = int(match.group(1))
                unit = match.group(2).lower()
                total += value * multipliers[unit]
            except (ValueError, KeyError) as e:
                raise SchedulingError.invalid_time_format(duration_str, str(e)) from e

        if total <= 0:
            raise SchedulingError.invalid_time_format(
                duration_str, "Duration must be positive"
            )

        return total

    def schedule_stop(self, alias: str, when: datetime) -> ScheduleTask:
        """Schedule a pod stop operation."""
        task = ScheduleTask(
            id=str(uuid.uuid4()),
            action="stop",
            alias=alias,
            when_epoch=int(when.astimezone().timestamp()),
            status=TaskStatus.PENDING,
            created_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        self.tasks.append(task)
        self._save_tasks()

        return task

    def cancel_task(self, task_id: str) -> ScheduleTask:
        """Cancel a scheduled task."""
        task = self.get_task(task_id)

        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return task  # Already finished

        task.status = TaskStatus.CANCELLED
        self._save_tasks()

        return task

    def get_task(self, task_id: str) -> ScheduleTask:
        """Get a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task

        raise SchedulingError.task_not_found(task_id)

    def get_due_tasks(self, current_epoch: int | None = None) -> list[ScheduleTask]:
        """Get tasks that are due for execution."""
        if current_epoch is None:
            current_epoch = int(datetime.now().timestamp())

        return [task for task in self.tasks if task.is_due(current_epoch)]

    def mark_task_completed(self, task_id: str) -> None:
        """Mark a task as completed."""
        task = self.get_task(task_id)
        task.status = TaskStatus.COMPLETED
        self._save_tasks()

    def mark_task_failed(self, task_id: str, error_message: str) -> None:
        """Mark a task as failed with an error message."""
        task = self.get_task(task_id)
        task.status = TaskStatus.FAILED
        task.last_error = error_message
        self._save_tasks()

    def ensure_macos_scheduler_installed(self, console: Console) -> None:
        """Install or update a launchd agent for macOS task execution."""
        if os.uname().sysname != "Darwin":
            return  # Only implement macOS launchd for now

        uv_path = shutil.which("uv")
        if not uv_path:
            console.print(
                "[yellow]⚠️  'uv' not found in PATH. Install via Homebrew: brew install uv[/yellow]"
            )
            return

        # Create necessary directories
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Get the path to this script for the scheduler-tick command
        script_path = str(Path(__file__).resolve())

        # Prepare environment variables
        env_vars = {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        }

        # Pass API key if available
        if not os.environ.get("RUNPOD_API_KEY") and API_KEY_FILE.exists():
            try:
                with API_KEY_FILE.open("r") as f:
                    key = f.read().strip()
                    if key:
                        env_vars["RUNPOD_API_KEY"] = key
            except Exception:
                pass  # Ignore key loading errors

        # Create plist configuration
        plist_dict = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [
                uv_path,
                "run",
                "--script",
                script_path,
                "scheduler-tick",
            ],
            "StartInterval": 60,  # Run every 60 seconds
            "RunAtLoad": True,
            "StandardOutPath": str(SCHEDULER_LOG_FILE),
            "StandardErrorPath": str(SCHEDULER_LOG_FILE),
            "EnvironmentVariables": env_vars,
        }

        # Write plist if missing or changed
        need_write = True
        if LAUNCHD_PLIST.exists():
            try:
                with LAUNCHD_PLIST.open("rb") as f:
                    import plistlib

                    existing = plistlib.load(f)
                need_write = existing != plist_dict
            except Exception:
                need_write = True

        if need_write:
            with LAUNCHD_PLIST.open("wb") as f:
                import plistlib

                plistlib.dump(plist_dict, f)

        # Manage the launchd agent
        uid = os.getuid()
        label_path = f"gui/{uid}/{LAUNCHD_LABEL}"

        # Check if already loaded
        exists = (
            subprocess.run(
                ["launchctl", "print", label_path],
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )

        if need_write and exists:
            # Replace the running agent to pick up changes
            subprocess.run(
                ["launchctl", "bootout", label_path],
                check=False,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_PLIST)],
                check=False,
                capture_output=True,
                text=True,
            )
        elif not exists:
            # Install the agent
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_PLIST)],
                check=False,
                capture_output=True,
                text=True,
            )

        # Kickstart to run immediately
        subprocess.run(
            ["launchctl", "kickstart", "-k", label_path],
            check=False,
            capture_output=True,
            text=True,
        )
