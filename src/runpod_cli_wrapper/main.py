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
    cursor_command,
    delete_command,
    destroy_command,
    list_command,
    schedule_cancel_command,
    schedule_clean_command,
    schedule_list_command,
    scheduler_tick_command,
    shell_command,
    start_command,
    stop_command,
    template_create_command,
    template_delete_command,
    template_list_command,
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

# Template sub-application
template_app = typer.Typer(help="Manage pod templates")


@app.command()
def create(
    alias: str = typer.Argument(
        None, help="SSH host alias to assign (e.g., alexs-machine)"
    ),
    gpu: str = typer.Option(None, "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        None, "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    template: str = typer.Option(
        None, "--template", help="Use a pod template (e.g., 'alex-ast')"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite alias if it exists"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show actions without creating"
    ),
):
    """Create a new RunPod using PyTorch 2.8 image, add alias, wait for SSH, and run setup scripts."""
    create_command(alias, gpu, storage, template, image, force, dry_run)


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


@schedule_app.command("clean")
def schedule_clean():
    """Remove tasks with status 'completed' or 'cancelled'."""
    schedule_clean_command()


@template_app.command("create")
def template_create(
    identifier: str = typer.Argument(
        ..., help="Template identifier (e.g., 'alex-ast')"
    ),
    alias_template: str = typer.Argument(
        ..., help="Alias template with {i} placeholder (e.g., 'alex-ast-{i}')"
    ),
    gpu: str = typer.Option(..., "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        ..., "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite template if it exists"
    ),
):
    """Create a new pod template."""
    template_create_command(identifier, alias_template, gpu, storage, image, force)


@template_app.command("list")
def template_list():
    """List all pod templates."""
    template_list_command()


@template_app.command("delete")
def template_delete(
    identifier: str = typer.Argument(..., help="Template identifier to delete"),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if template is missing"
    ),
):
    """Delete a pod template."""
    template_delete_command(identifier, missing_ok)


@app.command("scheduler-tick")
def scheduler_tick():
    """Execute due scheduled tasks (intended to be run by launchd every minute)."""
    scheduler_tick_command()


@app.command()
def cursor(
    alias: str = typer.Argument(..., help="Pod alias to connect to"),
    path: str = typer.Argument("/workspace", help="Remote path to open"),
):
    """Open Cursor editor with remote SSH connection to pod."""
    cursor_command(alias, path)


@app.command()
def shell(
    alias: str = typer.Argument(..., help="Pod alias to connect to"),
):
    """Open an interactive SSH shell to the pod."""
    shell_command(alias)


def main():
    """Main entry point with auto-cleanup of completed tasks."""
    # Auto-clean completed tasks before any command runs
    with contextlib.suppress(Exception):
        scheduler = Scheduler()
        scheduler.clean_completed_tasks()
        # Keep this silent in normal output

    # Mount sub-apps
    app.add_typer(schedule_app, name="schedule")
    app.add_typer(template_app, name="template")
    app()


if __name__ == "__main__":
    main()
