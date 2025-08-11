import contextlib
import json
import os
import plistlib
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import typer
from dateutil import parser as date_parser
from dateutil import tz
from rich.table import Table
from rich.text import Text

from .config import (
    API_KEY_FILE,
    LAUNCH_AGENTS_DIR,
    LAUNCHD_LABEL,
    LAUNCHD_PLIST,
    LOGS_DIR,
    SCHEDULE_FILE,
    SCHEDULER_LOG_FILE,
    console,
    ensure_config_dir_exists,
    setup_runpod_api,
)
from .pods import stop_impl


def _load_schedule_tasks() -> list[dict]:
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


def _save_schedule_tasks(tasks: list[dict]) -> None:
    ensure_config_dir_exists()
    tmp_path = SCHEDULE_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(tasks, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(SCHEDULE_FILE)


def _auto_clear_completed_tasks() -> int:
    tasks = _load_schedule_tasks()
    before = len(tasks)
    if before == 0:
        return 0
    pending_or_failed = [t for t in tasks if t.get("status") != "completed"]
    removed = before - len(pending_or_failed)
    if removed > 0:
        _save_schedule_tasks(pending_or_failed)
    return removed


def _now_local():
    return datetime.now(tz.tzlocal())


def _to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.tzlocal())
    return int(dt.astimezone(UTC).timestamp())


DURATION_RE = re_compile = None


def _duration_re():
    global DURATION_RE
    if DURATION_RE is None:
        import re

        DURATION_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
    return DURATION_RE


def parse_duration_to_seconds(text: str) -> int:
    total = 0
    for m in _duration_re().finditer(text.strip()):
        mult = {"d": 86400, "h": 3600, "m": 60, "s": 1}[m.group(2).lower()]
        total += int(m.group(1)) * mult
    if total <= 0:
        raise ValueError(f"Invalid --schedule-in value: {text}")
    return total


def parse_schedule_at(text: str, *, now: datetime | None = None) -> datetime:
    if not text:
        raise ValueError("Empty schedule string")
    text_stripped = text.strip()
    local_tz = tz.tzlocal()
    now = now or _now_local()
    import re

    m = re.match(r"^tomorrow\s+(\d{1,2}):(\d{2})$", text_stripped, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        target = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return target
    m = re.match(r"^(\d{1,2}):(\d{2})$", text_stripped)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return target
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text_stripped, fmt)
            return dt.replace(tzinfo=local_tz)
        except ValueError:
            pass
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


def schedule_task_stop(alias: str, when_dt: datetime) -> dict:
    tasks = _load_schedule_tasks()
    import uuid

    task = {
        "id": str(uuid.uuid4()),
        "action": "stop",
        "alias": alias,
        "when_epoch": _to_epoch_seconds(when_dt),
        "status": "pending",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tasks.append(task)
    _save_schedule_tasks(tasks)
    return task


def ensure_launchd_scheduler_installed() -> None:
    if os.uname().sysname != "Darwin":
        return
    uv_path = shutil.which("uv")
    if not uv_path:
        console.print(
            "[yellow]⚠️  'uv' not found in PATH. Install via Homebrew: brew install uv[/yellow]"
        )
        return
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # This module is imported by the script; call the entrypoint via uv run
    from pathlib import Path

    script_path = str(Path(__file__).resolve())
    # main script path
    program = [
        uv_path,
        "run",
        "--script",
        script_path.replace("/scheduler.py", "/main.py"),
        "scheduler-tick",
    ]

    env_vars = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    if not os.environ.get("RUNPOD_API_KEY") and API_KEY_FILE.exists():
        with contextlib.suppress(Exception), API_KEY_FILE.open("r") as f:
            key = (f.read() or "").strip()
            if key:
                env_vars["RUNPOD_API_KEY"] = key

    plist_dict = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program,
        "StartInterval": 60,
        "RunAtLoad": True,
        "StandardOutPath": str(SCHEDULER_LOG_FILE),
        "StandardErrorPath": str(SCHEDULER_LOG_FILE),
        "EnvironmentVariables": env_vars,
    }

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

    uid = os.getuid()
    label_path = f"gui/{uid}/{LAUNCHD_LABEL}"
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
    subprocess.run(
        ["launchctl", "kickstart", "-k", label_path],
        check=False,
        capture_output=True,
        text=True,
    )


def scheduler_tick_impl() -> None:
    tasks = _load_schedule_tasks()
    if not tasks:
        return
    now_epoch = int(datetime.now(UTC).timestamp())
    changed = False
    any_due = any(
        t.get("status") == "pending" and int(t.get("when_epoch", 0)) <= now_epoch
        for t in tasks
    )
    if any_due:
        try:
            setup_runpod_api()
        except Exception:
            return
    for task in tasks:
        if task.get("status") != "pending":
            continue
        if int(task.get("when_epoch", 0)) > now_epoch:
            continue
        action = task.get("action")
        alias = task.get("alias")
        try:
            if action == "stop" and alias:
                stop_impl(alias)
                task["status"] = "completed"
                changed = True
        except Exception as e:
            task["status"] = "failed"
            task["last_error"] = str(e)
            changed = True
    if changed:
        _save_schedule_tasks(tasks)


# Expose a Typer sub-app for schedule management
schedule_app = typer.Typer(help="Manage scheduled tasks")


@schedule_app.command("list")
def schedule_list_cmd():
    tasks = _load_schedule_tasks()
    from dateutil import tz as _tz

    if not tasks:
        console.print("[yellow]No scheduled tasks.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="magenta")
    table.add_column("Action", style="white")
    table.add_column("Alias", style="green")
    table.add_column("When (local)", style="white")
    table.add_column("Status", style="white")
    for t in tasks:
        when_epoch = int(t.get("when_epoch", 0))
        when_local = (
            datetime.fromtimestamp(when_epoch, _tz.tzlocal()).strftime(
                "%Y-%m-%d %H:%M %Z"
            )
            if when_epoch
            else "-"
        )
        status = t.get("status", "?")
        status_text = (
            Text(status, style="bold green")
            if status == "pending"
            else Text(status, style="yellow" if status == "failed" else "dim")
        )
        table.add_row(
            t.get("id", "-"),
            t.get("action", "-"),
            t.get("alias", "-"),
            when_local,
            status_text,
        )
    console.print(table)


@schedule_app.command("cancel")
def schedule_cancel_cmd(task_id: str = typer.Argument(..., help="Task id to cancel")):
    tasks = _load_schedule_tasks()
    found = False
    for t in tasks:
        if t.get("id") == task_id:
            if t.get("status") in {"completed", "cancelled"}:
                console.print(
                    f"[yellow]Task {task_id} is already {t.get('status')}.[/yellow]"
                )
                found = True
                break
            t["status"] = "cancelled"
            found = True
            break
    if not found:
        typer.echo(f"❌ Task id not found: {task_id}", err=True)
        raise typer.Exit(1)
    _save_schedule_tasks(tasks)
    console.print(f"✅ Cancelled task [bold]{task_id}[/bold].")


@schedule_app.command("clear-completed")
def schedule_clear_completed_cmd():
    removed = _auto_clear_completed_tasks()
    if removed:
        console.print(f"✅ Removed [bold]{removed}[/bold] completed task(s).")
    else:
        console.print("No completed tasks to remove.")
