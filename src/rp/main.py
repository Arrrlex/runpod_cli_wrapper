"""
Main entry point for the RunPod CLI wrapper (refactored version).

This module provides the main application entry point and command-line interface
using the refactored service layer architecture.
"""

import contextlib
import json

import typer
from typer.core import TyperGroup

from rp.cli.commands import (
    clean_command,
    config_command,
    create_command,
    cursor_command,
    destroy_command,
    list_command,
    schedule_cancel_command,
    schedule_list_command,
    scheduler_tick_command,
    shell_command,
    show_command,
    start_command,
    stop_command,
    template_create_command,
    template_delete_command,
    template_list_command,
    track_command,
    untrack_command,
)
from rp.config import POD_CONFIG_FILE
from rp.core.models import AppConfig
from rp.core.scheduler import Scheduler


def complete_alias(incomplete: str) -> list[str]:
    """Provide tab completion for pod aliases."""
    try:
        # Load config from disk
        with POD_CONFIG_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                if (
                    "aliases" in data
                    or "pod_templates" in data
                    or "pod_metadata" in data
                ):
                    config = AppConfig.model_validate(data)
                else:
                    config = AppConfig(
                        aliases={str(k): str(v) for k, v in data.items()}
                    )
            else:
                config = AppConfig()

        aliases = list(config.get_all_aliases().keys())
        return [alias for alias in aliases if alias.startswith(incomplete)]
    except Exception:
        return []


def complete_template(incomplete: str) -> list[str]:
    """Provide tab completion for template identifiers."""
    try:
        # Load config from disk
        with POD_CONFIG_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                if (
                    "aliases" in data
                    or "pod_templates" in data
                    or "pod_metadata" in data
                ):
                    config = AppConfig.model_validate(data)
                else:
                    config = AppConfig(
                        aliases={str(k): str(v) for k, v in data.items()}
                    )
            else:
                config = AppConfig()

        templates = list(config.pod_templates.keys())
        return [template for template in templates if template.startswith(incomplete)]
    except Exception:
        return []


class OrderedGroup(TyperGroup):
    """Custom group to control command order in help."""

    def list_commands(self, _):
        preferred = ["create", "destroy", "track"]
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
    template: str = typer.Argument(
        None,
        help="Template identifier to use (e.g., 'training-template')",
        autocompletion=complete_template,
    ),
    alias: str = typer.Option(
        None, "--alias", help="SSH host alias to assign (e.g., alexs-machine)"
    ),
    gpu: str = typer.Option(None, "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        None, "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    container_disk: str = typer.Option(
        None, "--container-disk", help="Container disk size like '20GB' (default: 20GB)"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    config: list[str] = typer.Option(
        None,
        "--config",
        help="Config key=value pairs (e.g., 'path=/workspace/project')",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite alias if it exists"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show actions without creating"
    ),
):
    """Create a new RunPod instance, add alias, wait for SSH, and run setup scripts."""
    create_command(
        alias, gpu, storage, container_disk, template, image, config, force, dry_run
    )


@app.command()
def start(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
):
    """Start and configure a RunPod instance."""
    start_command(host_alias)


@app.command()
def stop(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
    at: str | None = typer.Option(
        None,
        "--at",
        help='Schedule at a time, e.g. "22:00", "2025-01-03 09:30", or "tomorrow 09:30"',
    ),
    in_: str | None = typer.Option(
        None,
        "--in",
        help='Schedule after a duration, e.g. "3h", "45m", "1d2h30m"',
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would happen without performing the action",
    ),
):
    """Stop a RunPod instance, optionally scheduling for later."""
    stop_command(host_alias, at, in_, dry_run)


@app.command()
def destroy(
    host_alias: str = typer.Argument(
        None,
        help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)",
        autocompletion=complete_alias,
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
):
    """Terminate a pod, remove SSH config, and delete the alias mapping."""
    destroy_command(host_alias, force)


@app.command()
def track(
    alias: str = typer.Argument(
        ..., help="Alias name to assign to an existing RunPod pod id"
    ),
    pod_id: str = typer.Argument(..., help="RunPod pod id (e.g., 89qgenjznh5t2j)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite if alias already exists"
    ),
):
    """Track an existing RunPod pod with an alias."""
    track_command(alias, pod_id, force)


@app.command()
def untrack(
    alias: str = typer.Argument(
        None, help="Alias name to remove", autocompletion=complete_alias
    ),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if alias is missing"
    ),
):
    """Stop tracking a pod (removes alias mapping)."""
    untrack_command(alias, missing_ok)


@app.command("list")
def list_aliases():
    """List all aliases as a table: Alias, ID, Status (running, stopped, invalid)."""
    list_command()


@app.command()
def show(
    alias: str = typer.Argument(
        None, help="Pod alias to show details for", autocompletion=complete_alias
    ),
):
    """Show detailed information about a pod."""
    show_command(alias)


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


@template_app.command("create")
def template_create(
    identifier: str = typer.Argument(
        ..., help="Template identifier (e.g., 'alex-ast')"
    ),
    alias_pattern: str = typer.Option(
        ...,
        "--alias-pattern",
        help="Alias pattern with {i} placeholder (e.g., 'alex-ast-{i}')",
    ),
    gpu: str = typer.Option(..., "--gpu", help="GPU spec like '2xA100'"),
    storage: str = typer.Option(
        ..., "--storage", help="Volume size like '500GB' or '1TB'"
    ),
    container_disk: str = typer.Option(
        None, "--container-disk", help="Container disk size like '20GB' (default: 20GB)"
    ),
    image: str = typer.Option(
        None,
        "--image",
        help="Docker image to use (default: runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04)",
    ),
    config: list[str] = typer.Option(
        None,
        "--config",
        help="Config key=value pairs (e.g., 'path=/workspace/project')",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite template if it exists"
    ),
):
    """Create a new pod template."""
    template_create_command(
        identifier, alias_pattern, gpu, storage, container_disk, image, config, force
    )


@template_app.command("list")
def template_list():
    """List all pod templates."""
    template_list_command()


@template_app.command("delete")
def template_delete(
    identifier: str = typer.Argument(
        ..., help="Template identifier to delete", autocompletion=complete_template
    ),
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
    alias: str = typer.Argument(
        None, help="Pod alias to connect to", autocompletion=complete_alias
    ),
    path: str = typer.Argument(
        None, help="Remote path to open (uses config default or /workspace)"
    ),
):
    """Open Cursor editor with remote SSH connection to pod."""
    cursor_command(alias, path)


@app.command()
def shell(
    alias: str = typer.Argument(
        None, help="Pod alias to connect to", autocompletion=complete_alias
    ),
):
    """Open an interactive SSH shell to the pod."""
    shell_command(alias)


@app.command()
def config(
    alias: str = typer.Argument(
        None, help="Pod alias to configure", autocompletion=complete_alias
    ),
    args: list[str] = typer.Argument(
        None, help="Either 'key' to get value, or 'key=value' pairs to set"
    ),
):
    """Get or set configuration for a pod.

    Examples:
      rp config my-pod path                # Get value
      rp config my-pod path=/workspace/x   # Set value
      rp config my-pod path=/x path2=/y    # Set multiple
    """
    config_command(alias, args or [])


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
