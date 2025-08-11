"""
Main entry point for the RunPod CLI wrapper (refactored version).

This module provides the main application entry point and command-line interface
using the refactored service layer architecture.
"""

import contextlib

import typer
from typer.core import TyperGroup

from runpod_cli_wrapper.cli.commands import (
    add_command,
    clean_command,
    create_command,
    delete_command,
    destroy_command,
    list_command,
    schedule_cancel_command,
    schedule_clear_completed_command,
    schedule_list_command,
    scheduler_tick_command,
    start_command,
    stop_command,
)
from runpod_cli_wrapper.core.scheduler import Scheduler


class OrderedGroup(TyperGroup):
    """Custom group to control command order in help."""

    def list_commands(self, _):
        preferred = ["create", "destroy", "add"]
        all_cmds = list(self.commands.keys())
        rest = [c for c in all_cmds if c not in preferred]
        return preferred + rest


# Main application
app = typer.Typer(
    help="RunPod utility for starting and stopping pods", cls=OrderedGroup
)

# Schedule sub-application
schedule_app = typer.Typer(help="Manage scheduled tasks")


@app.command()
def create(
    alias: str = typer.Argument(
        ..., help="SSH host alias to assign (e.g., alexs-machine)"
    ),
    gpu: str = typer.Option(..., "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        ..., "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite alias if it exists"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show actions without creating"
    ),
):
    """Create a new RunPod using PyTorch 2.8 image, add alias, wait for SSH, and run setup scripts."""
    create_command(alias, gpu, storage, force, dry_run)


@app.command()
def start(
    host_alias: str = typer.Argument(
        ..., help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)"
    ),
):
    """Start and configure a RunPod instance."""
    start_command(host_alias)


@app.command()
def stop(
    host_alias: str = typer.Argument(
        ..., help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)"
    ),
    schedule_at: str | None = typer.Option(
        None,
        "--schedule-at",
        help='Schedule at a time, e.g. "22:00", "2025-01-03 09:30", or "tomorrow 09:30"',
    ),
    schedule_in: str | None = typer.Option(
        None,
        "--schedule-in",
        help='Schedule after a duration, e.g. "3h", "45m", "1d2h30m"',
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would happen without performing the action",
    ),
):
    """Stop a RunPod instance, optionally scheduling for later."""
    stop_command(host_alias, schedule_at, schedule_in, dry_run)


@app.command()
def destroy(
    host_alias: str = typer.Argument(
        ..., help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)"
    ),
):
    """Terminate a pod, remove SSH config, and delete the alias mapping."""
    destroy_command(host_alias)


@app.command()
def add(
    alias: str = typer.Argument(
        ..., help="Alias name to assign to an existing RunPod pod id"
    ),
    pod_id: str = typer.Argument(..., help="RunPod pod id (e.g., 89qgenjznh5t2j)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite if alias already exists"
    ),
):
    """Add or update an existing RunPod pod."""
    add_command(alias, pod_id, force)


@app.command()
def delete(
    alias: str = typer.Argument(..., help="Alias name to remove"),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if alias is missing"
    ),
):
    """Delete an alias mapping."""
    delete_command(alias, missing_ok)


@app.command("list")
def list_aliases():
    """List all aliases as a table: Alias, ID, Status (running, stopped, invalid)."""
    list_command()


@app.command()
def clean():
    """Remove invalid aliases and prune rp-managed SSH blocks no longer valid."""
    clean_command()


@schedule_app.command("list")
def schedule_list():
    """List scheduled tasks."""
    schedule_list_command()


@schedule_app.command("cancel")
def schedule_cancel(task_id: str = typer.Argument(..., help="Task id to cancel")):
    """Cancel a scheduled task by id (sets status to 'cancelled')."""
    schedule_cancel_command(task_id)


@schedule_app.command("clear-completed")
def schedule_clear_completed():
    """Remove tasks with status 'completed'."""
    schedule_clear_completed_command()


@app.command("scheduler-tick")
def scheduler_tick():
    """Execute due scheduled tasks (intended to be run by launchd every minute)."""
    scheduler_tick_command()


def main():
    """Main entry point with auto-cleanup of completed tasks."""
    # Auto-clear completed tasks before any command runs
    with contextlib.suppress(Exception):
        scheduler = Scheduler()
        scheduler.clear_completed_tasks()
        # Keep this silent in normal output

    # Mount schedule sub-app
    app.add_typer(schedule_app, name="schedule")
    app()


if __name__ == "__main__":
    main()
