"""
CLI command implementations using the service layer.

This module implements all the CLI commands using the refactored service layer,
providing clean separation between CLI interface and business logic.
"""

from datetime import datetime, timedelta

from dateutil import tz
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from runpod_cli_wrapper.cli.utils import (
    console,
    display_pods_table,
    display_schedule_table,
    handle_cli_error,
    parse_gpu_spec,
    parse_storage_spec,
    run_setup_scripts,
    setup_api_client,
)
from runpod_cli_wrapper.core.models import PodCreateRequest, SSHConfig
from runpod_cli_wrapper.core.pod_manager import PodManager
from runpod_cli_wrapper.core.scheduler import Scheduler
from runpod_cli_wrapper.core.ssh_manager import SSHManager
from runpod_cli_wrapper.utils.errors import SchedulingError

# Initialize services (will be properly injected in production)
_pod_manager: PodManager | None = None
_scheduler: Scheduler | None = None
_ssh_manager: SSHManager | None = None


def get_pod_manager() -> PodManager:
    """Get or create PodManager instance."""
    global _pod_manager  # noqa: PLW0603
    if _pod_manager is None:
        api_client = setup_api_client()
        _pod_manager = PodManager(api_client)
    return _pod_manager


def get_scheduler() -> Scheduler:
    """Get or create Scheduler instance."""
    global _scheduler  # noqa: PLW0603
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def get_ssh_manager() -> SSHManager:
    """Get or create SSHManager instance."""
    global _ssh_manager  # noqa: PLW0603
    if _ssh_manager is None:
        _ssh_manager = SSHManager()
    return _ssh_manager


def create_command(
    alias: str,
    gpu: str,
    storage: str,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Create a new RunPod using PyTorch 2.8 image."""
    try:
        gpu_spec = parse_gpu_spec(gpu)
        volume_gb = parse_storage_spec(storage)

        request = PodCreateRequest(
            alias=alias,
            gpu_spec=gpu_spec,
            volume_gb=volume_gb,
            force=force,
            dry_run=dry_run,
        )

        pod_manager = get_pod_manager()

        console.print(
            f"🚀 Creating pod '[bold]{alias}[/bold]': "
            f"image=[dim]{request.image}[/dim], "
            f"GPU={gpu_spec}, volume={volume_gb}GB"
        )

        if dry_run:
            console.print("[bold]DRY RUN[/bold] No changes were made.")
            return

        # Create pod with progress indication
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("Creating pod…", total=None)
            pod = pod_manager.create_pod(request)
            progress.update(task, description="Pod created successfully")

        console.print(f"✅ Saved alias '[bold]{alias}[/bold]' -> {pod.id}")

        # Configure SSH
        if pod.ip_address and pod.ssh_port:
            console.print("📝 Updating SSH config…")
            ssh_config = SSHConfig(
                alias=alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")

        # Run setup scripts
        run_setup_scripts(alias)

    except Exception as e:
        handle_cli_error(e)


def start_command(alias: str) -> None:
    """Start/resume a RunPod instance."""
    try:
        pod_manager = get_pod_manager()

        console.print(f"🚀 Starting pod '[bold]{alias}[/bold]'…")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("Starting pod…", total=None)
            pod = pod_manager.start_pod(alias)
            progress.update(task, description="Pod is running")

        console.print("✅ Pod is now [bold green]RUNNING[/bold green].")

        # Update SSH config
        if pod.ip_address and pod.ssh_port:
            console.print(f"Found IP: [bold]{pod.ip_address}[/bold]")
            console.print(f"Found Port: [bold]{pod.ssh_port}[/bold]")

            ssh_config = SSHConfig(
                alias=alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")

        # Run setup scripts
        run_setup_scripts(alias)

    except Exception as e:
        handle_cli_error(e)


def stop_command(
    alias: str,
    schedule_at: str | None = None,
    schedule_in: str | None = None,
    dry_run: bool = False,
) -> None:
    """Stop a RunPod instance, optionally scheduling for later."""
    try:
        # Validate alias exists
        pod_manager = get_pod_manager()
        pod_manager.get_pod_id(alias)  # Raises if not found

        if schedule_at and schedule_in:
            raise SchedulingError.conflicting_options("--schedule-at", "--schedule-in")

        if schedule_at or schedule_in:
            scheduler = get_scheduler()

            if schedule_at:
                when_dt = scheduler.parse_time_string(schedule_at)
            else:
                seconds = scheduler.parse_duration_string(schedule_in or "")
                when_dt = datetime.now(tz.tzlocal()) + timedelta(seconds=seconds)

            local_str = when_dt.strftime("%Y-%m-%d %H:%M %Z")
            now = datetime.now(tz.tzlocal())
            rel_seconds = max(0, int((when_dt - now).total_seconds()))
            rel_desc = (
                f"in {rel_seconds // 3600}h{(rel_seconds % 3600) // 60:02d}m"
                if rel_seconds >= 60
                else f"in {rel_seconds}s"
            )

            if dry_run:
                console.print(
                    f"⏰ [bold]DRY RUN[/bold] Would schedule stop of '[bold]{alias}[/bold]' "
                    f"at {local_str} ({rel_desc})."
                )
                return

            task = scheduler.schedule_stop(alias, when_dt)
            console.print(
                f"⏰ Scheduled stop of '[bold]{alias}[/bold]' at [bold]{local_str}[/bold] "
                f"({rel_desc}). [dim](id={task.id})[/dim]"
            )

            # Ensure scheduler is running on macOS
            scheduler.ensure_macos_scheduler_installed(console)
            return

        if dry_run:
            console.print(
                f"[bold]DRY RUN[/bold] Would stop '[bold]{alias}[/bold]' now."
            )
            return

        # Immediate stop
        console.print(f"🛑 Stopping pod '[bold]{alias}[/bold]'…")
        pod_manager.stop_pod(alias)
        console.print("✅ Pod has been stopped.")

        # Remove SSH config
        ssh_manager = get_ssh_manager()
        removed = ssh_manager.remove_host_config(alias)
        if removed:
            console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")

    except Exception as e:
        handle_cli_error(e)


def destroy_command(alias: str) -> None:
    """Terminate a pod, remove SSH config, and delete the alias."""
    try:
        pod_manager = get_pod_manager()

        console.print(f"🔥 Destroying pod '[bold]{alias}[/bold]'…")
        pod_id = pod_manager.destroy_pod(alias)
        console.print(f"✅ Terminated pod [bold]{pod_id}[/bold].")

        # Clean SSH config
        ssh_manager = get_ssh_manager()
        removed = ssh_manager.remove_host_config(alias)
        if removed:
            console.print(f"🧹 Removed SSH config block for '[bold]{alias}[/bold]'")

        console.print(
            f"🗑️  Removed alias '[bold]{alias}[/bold]' from local configuration."
        )

    except Exception as e:
        handle_cli_error(e)


def add_command(alias: str, pod_id: str, force: bool = False) -> None:
    """Add an alias for an existing RunPod."""
    try:
        pod_manager = get_pod_manager()
        pod_manager.add_alias(alias, pod_id, force)
        console.print(f"✅ Saved alias '[bold]{alias}[/bold]' -> {pod_id}")

    except Exception as e:
        handle_cli_error(e)


def delete_command(alias: str, missing_ok: bool = False) -> None:
    """Delete an alias mapping."""
    try:
        pod_manager = get_pod_manager()
        pod_id = pod_manager.remove_alias(alias, missing_ok)

        if pod_id:
            console.print(f"✅ Removed alias '[bold]{alias}[/bold]' (was {pod_id})")
        else:
            console.print(f"i  Alias '[bold]{alias}[/bold]' not found; nothing to do.")

    except Exception as e:
        handle_cli_error(e)


def list_command() -> None:
    """List all aliases with their status."""
    try:
        pod_manager = get_pod_manager()
        pods = pod_manager.list_pods()
        display_pods_table(pods)

    except Exception as e:
        handle_cli_error(e)


def clean_command() -> None:
    """Remove invalid aliases and prune SSH blocks."""
    try:
        pod_manager = get_pod_manager()
        removed_aliases = pod_manager.clean_invalid_aliases()

        if removed_aliases:
            console.print(
                f"✅ Removed [bold]{removed_aliases}[/bold] invalid alias(es)."
            )
        else:
            console.print("✅ No invalid aliases found.")

        # Prune SSH blocks
        ssh_manager = get_ssh_manager()
        valid_aliases = set(pod_manager.aliases.keys())
        removed_blocks = ssh_manager.prune_managed_blocks(valid_aliases)

        if removed_blocks:
            console.print(
                f"🧹 Removed [bold]{removed_blocks}[/bold] orphaned SSH config blocks."
            )
        else:
            console.print("✅ No orphaned SSH config blocks to prune.")

    except Exception as e:
        handle_cli_error(e)


def schedule_list_command() -> None:
    """List scheduled tasks."""
    try:
        scheduler = get_scheduler()
        display_schedule_table(scheduler.tasks)

    except Exception as e:
        handle_cli_error(e)


def schedule_cancel_command(task_id: str) -> None:
    """Cancel a scheduled task."""
    try:
        scheduler = get_scheduler()
        task = scheduler.cancel_task(task_id)

        if task.status.value in {"completed", "cancelled"}:
            console.print(
                f"[yellow]Task {task_id} is already {task.status.value}.[/yellow]"
            )
        else:
            console.print(f"✅ Cancelled task [bold]{task_id}[/bold].")

    except Exception as e:
        handle_cli_error(e)


def schedule_clear_completed_command() -> None:
    """Remove completed tasks."""
    try:
        scheduler = get_scheduler()
        removed = scheduler.clear_completed_tasks()

        if removed:
            console.print(f"✅ Removed [bold]{removed}[/bold] completed task(s).")
        else:
            console.print("No completed tasks to remove.")

    except Exception as e:
        handle_cli_error(e)


def scheduler_tick_command() -> None:
    """Execute due scheduled tasks (called by launchd)."""
    try:
        scheduler = get_scheduler()
        due_tasks = scheduler.get_due_tasks()

        if not due_tasks:
            return

        # Initialize pod manager for task execution
        pod_manager = get_pod_manager()

        for task in due_tasks:
            try:
                if task.action == "stop":
                    pod_manager.stop_pod(task.alias)

                    # Remove SSH config
                    ssh_manager = get_ssh_manager()
                    ssh_manager.remove_host_config(task.alias)

                    scheduler.mark_task_completed(task.id)
            except Exception as e:
                scheduler.mark_task_failed(task.id, str(e))

    except Exception:
        # Silently fail for scheduler tick to avoid noise
        pass
