"""
Scheduling utilities for the RunPod CLI wrapper.

This module provides functionality for managing scheduled tasks, including loading,
saving, and clearing completed tasks. It includes support for macOS launchd
agents to run scheduled tasks.
"""

import contextlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
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


def load_schedule_tasks() -> list[dict]:
    """Load scheduled tasks from file; return empty list on missing/invalid."""
    try:
        with SCHEDULE_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


def save_schedule_tasks(tasks: list[dict]) -> None:
    ensure_config_dir_exists()
    tmp_path = SCHEDULE_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(tasks, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(SCHEDULE_FILE)


def auto_clear_completed_tasks() -> int:
    """Remove tasks with status 'completed' and persist if any were removed. Returns count removed."""
    tasks = load_schedule_tasks()
    before = len(tasks)
    if before == 0:
        return 0
    pending_or_failed = [t for t in tasks if t.get("status") != "completed"]
    removed = before - len(pending_or_failed)
    if removed > 0:
        save_schedule_tasks(pending_or_failed)
    return removed


def now_local() -> datetime:
    return datetime.now(tz.tzlocal())


def to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        # Assume local if naive
        dt = dt.replace(tzinfo=tz.tzlocal())
    return int(dt.astimezone(UTC).timestamp())


def parse_schedule_at(text: str, *, now: datetime | None = None) -> datetime:
    """Parse an absolute time string into an aware datetime (local tz).

    Supported examples:
      - "HH:MM" (today, or tomorrow if past)
      - "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM" (any date)
      - "tomorrow HH:MM"
      - Otherwise, defer to dateutil.parser with local timezone default
    """
    if not text:
        raise ValueError("Empty schedule string")
    text_stripped = text.strip()
    local_tz = tz.tzlocal()
    now = now or now_local()

    # tomorrow HH:MM
    m = re.match(r"^tomorrow\s+(\d{1,2}):(\d{2})$", text_stripped, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        target = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return target

    # HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", text_stripped)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return target

    # Try common explicit formats first for speed
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text_stripped, fmt)
            return dt.replace(tzinfo=local_tz)
        except ValueError:
            pass

    # Fallback to dateutil.parser with local tz as default
    try:
        dt = date_parser.parse(
            text_stripped,
            default=now.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)
        return dt
    except Exception as e:
        raise ValueError(f"Invalid --schedule-at value: {text}: {e}") from e


DURATION_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
DURATION_MULTIPLIERS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration_to_seconds(text: str) -> int:
    total = 0
    for m in DURATION_RE.finditer(text.strip()):
        total += int(m.group(1)) * DURATION_MULTIPLIERS[m.group(2).lower()]
    if total <= 0:
        raise ValueError(f"Invalid --schedule-in value: {text}")
    return total


def schedule_task_stop(alias: str, when_dt: datetime) -> dict:
    tasks = load_schedule_tasks()
    task = {
        "id": str(uuid.uuid4()),
        "action": "stop",
        "alias": alias,
        "when_epoch": to_epoch_seconds(when_dt),
        "status": "pending",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tasks.append(task)
    save_schedule_tasks(tasks)
    return task


def ensure_launchd_scheduler_installed(console: Console) -> None:
    """Install or update a per-user launchd agent to run scheduler-tick every 60s (macOS)."""
    if os.uname().sysname != "Darwin":
        return  # Only implement macOS launchd for now

    uv_path = shutil.which("uv")
    if not uv_path:
        console.print(
            "[yellow]⚠️  'uv' not found in PATH. Install via Homebrew: brew install uv[/yellow]"
        )
        return

    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    script_path = str(Path(__file__).resolve())

    env_vars = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    }
    # Pass stored API key to the agent if available and env not already set
    if not os.environ.get("RUNPOD_API_KEY") and API_KEY_FILE.exists():
        with contextlib.suppress(Exception), API_KEY_FILE.open("r") as f:
            key = (f.read() or "").strip()
            if key:
                env_vars["RUNPOD_API_KEY"] = key

    plist_dict = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [uv_path, "run", "--script", script_path, "scheduler-tick"],
        "StartInterval": 60,
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
                existing = plistlib.load(f)
            need_write = existing != plist_dict
        except Exception:
            need_write = True
    if need_write:
        with LAUNCHD_PLIST.open("wb") as f:
            plistlib.dump(plist_dict, f)

    # Load or kickstart the agent, avoiding noisy bootstrap errors
    uid = os.getuid()
    label_path = f"gui/{uid}/{LAUNCHD_LABEL}"
    # Check if already installed
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
        # Replace the running agent to pick up plist changes
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
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_PLIST)],
            check=False,
            capture_output=True,
            text=True,
        )

    # Always kickstart to run promptly
    subprocess.run(
        ["launchctl", "kickstart", "-k", label_path],
        check=False,
        capture_output=True,
        text=True,
    )
