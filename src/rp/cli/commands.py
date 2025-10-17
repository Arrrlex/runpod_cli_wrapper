"""
CLI command implementations using the service layer.

This module implements all the CLI commands using the refactored service layer,
providing clean separation between CLI interface and business logic.
"""

from datetime import datetime, timedelta

import typer
from dateutil import tz
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from rp.cli.utils import (
    console,
    display_pods_table,
    display_schedule_table,
    handle_cli_error,
    parse_config_flags,
    parse_gpu_spec,
    parse_storage_spec,
    run_setup_scripts,
    setup_api_client,
)
from rp.core.models import PodCreateRequest, PodTemplate, SSHConfig
from rp.core.pod_manager import PodManager
from rp.core.scheduler import Scheduler
from rp.core.ssh_manager import SSHManager
from rp.utils.errors import SchedulingError

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


def _auto_clean() -> None:
    """Silently perform cleanup tasks (invalid aliases, SSH blocks, completed tasks)."""
    try:
        pod_manager = get_pod_manager()
        ssh_manager = get_ssh_manager()
        scheduler = get_scheduler()

        # Clean invalid aliases
        pod_manager.clean_invalid_aliases()

        # Prune SSH blocks
        valid_aliases = set(pod_manager.aliases.keys())
        ssh_manager.prune_managed_blocks(valid_aliases)

        # Clean completed/cancelled scheduled tasks
        scheduler.clean_completed_tasks()
    except Exception:
        # Silently fail - don't disrupt the user's workflow
        pass


def create_command(  # noqa: PLR0915  # Function complexity acceptable for main command
    alias: str | None = None,
    gpu: str | None = None,
    storage: str | None = None,
    container_disk: str | None = None,
    template: str | None = None,
    image: str | None = None,
    config: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Create a new RunPod using PyTorch 2.8 image."""
    try:
        pod_manager = get_pod_manager()

        # Validate arguments
        if not template and not (alias and gpu and storage):
            raise ValueError(
                "Must specify either a template (as first argument) or all of (--alias, --gpu, --storage)"
            )

        if template:
            # Use template mode (with optional alias override)
            if alias:
                console.print(
                    f"🚀 Creating pod '[bold]{alias}[/bold]' from template '[bold]{template}[/bold]'"
                )
            else:
                console.print(
                    f"🚀 Creating pod from template '[bold]{template}[/bold]'"
                )

            if dry_run:
                # Show what would be created
                template_obj = pod_manager.get_template(template)
                if alias:
                    proposed_alias = alias
                else:
                    next_index = pod_manager.config.find_next_alias_index(
                        template_obj.alias_template
                    )
                    proposed_alias = template_obj.alias_template.format(i=next_index)

                console.print("[bold]DRY RUN[/bold] Would create:")
                console.print(f"   Alias: {proposed_alias}")
                console.print(f"   GPU: {template_obj.gpu_spec}")
                console.print(f"   Storage: {template_obj.storage_spec}")
                return

            # Create pod with progress indication
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Creating pod from template…", total=None)
                pod = pod_manager.create_pod_from_template(
                    template, force, dry_run, alias_override=alias
                )
                progress.update(task, description="Pod created successfully")

            final_alias = pod.alias
            template_used = template
        else:
            # Use direct specification mode - at this point we know these are not None due to validation
            assert alias is not None
            assert gpu is not None
            assert storage is not None

            gpu_spec = parse_gpu_spec(gpu)
            volume_gb = parse_storage_spec(storage)

            request_kwargs = {
                "alias": alias,
                "gpu_spec": gpu_spec,
                "volume_gb": volume_gb,
                "force": force,
                "dry_run": dry_run,
            }

            # Add container disk if specified, otherwise use default (20GB)
            if container_disk is not None:
                container_disk_gb = parse_storage_spec(container_disk)
                request_kwargs["container_disk_gb"] = container_disk_gb

            # Add image if specified
            if image is not None:
                request_kwargs["image"] = image

            request = PodCreateRequest(**request_kwargs)  # type: ignore[arg-type]

            console.print(
                f"🚀 Creating pod '[bold]{alias}[/bold]': "
                f"image=[dim]{request.image}[/dim], "
                f"GPU={gpu_spec}, volume={volume_gb}GB, container_disk={request.container_disk_gb}GB"
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

            final_alias = alias
            template_used = None
            # Store for summary
            final_gpu_spec = gpu_spec
            final_volume_gb = volume_gb

        # At this point final_alias should never be None
        assert final_alias is not None

        console.print(f"✅ Saved alias '[bold]{final_alias}[/bold]' -> {pod.id}")

        # Apply config values if provided via --config flag
        if config:
            pod_config = parse_config_flags(config)
            for key, value in pod_config.model_dump().items():
                if value is not None:
                    pod_manager.set_pod_config(final_alias, key, value)
                    console.print(f"⚙️  Set config '{key}' = '{value}'")

        # Configure SSH
        if pod.ip_address and pod.ssh_port:
            console.print("📝 Updating SSH config…")
            ssh_config = SSHConfig(
                alias=final_alias,
                pod_id=pod.id,
                hostname=pod.ip_address,
                port=pod.ssh_port,
            )
            ssh_manager = get_ssh_manager()
            ssh_manager.update_host_config(ssh_config)
            console.print("✅ SSH config updated successfully.")

        # Run setup scripts
        run_setup_scripts(final_alias)

        # Print summary
        if template_used:
            console.print(
                f"🎉 Created pod '[bold green]{final_alias}[/bold green]' from template '[bold blue]{template_used}[/bold blue]'"
            )
        else:
            console.print(
                f"🎉 Created pod '[bold green]{final_alias}[/bold green]' with [bold yellow]{final_gpu_spec}[/bold yellow] GPU and [bold yellow]{final_volume_gb}GB[/bold yellow] storage"
            )

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

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

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def stop_command(
    alias: str,
    at: str | None = None,
    in_: str | None = None,
    dry_run: bool = False,
) -> None:
    """Stop a RunPod instance, optionally scheduling for later."""
    try:
        # Validate alias exists
        pod_manager = get_pod_manager()
        pod_manager.get_pod_id(alias)  # Raises if not found

        if at and in_:
            raise SchedulingError.conflicting_options("--at", "--in")

        if at or in_:
            scheduler = get_scheduler()

            if at:
                when_dt = scheduler.parse_time_string(at)
            else:
                seconds = scheduler.parse_duration_string(in_ or "")
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

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def destroy_command(alias: str, force: bool = False) -> None:
    """Terminate a pod, remove SSH config, and delete the alias."""
    try:
        pod_manager = get_pod_manager()

        # Confirm destruction unless force is set
        if not force:
            response = typer.confirm(
                f"⚠️  Are you sure you want to destroy pod '{alias}'? This action cannot be undone."
            )
            if not response:
                console.print("❌ Destruction cancelled.")
                raise typer.Exit(0)

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

        # Auto-clean invalid aliases and completed tasks
        _auto_clean()

    except Exception as e:
        handle_cli_error(e)


def track_command(alias: str, pod_id: str, force: bool = False) -> None:
    """Track an existing RunPod pod with an alias."""
    try:
        pod_manager = get_pod_manager()
        pod_manager.add_alias(alias, pod_id, force)
        console.print(f"✅ Now tracking '[bold]{alias}[/bold]' -> {pod_id}")

    except Exception as e:
        handle_cli_error(e)


def untrack_command(alias: str, missing_ok: bool = False) -> None:
    """Stop tracking a pod (removes alias mapping)."""
    try:
        pod_manager = get_pod_manager()
        pod_id = pod_manager.remove_alias(alias, missing_ok)

        if pod_id:
            console.print(f"✅ Stopped tracking '[bold]{alias}[/bold]' (was {pod_id})")
        else:
            console.print(f"i  Alias '[bold]{alias}[/bold]' not found; nothing to do.")

    except Exception as e:
        handle_cli_error(e)


def list_command() -> None:
    """List all aliases with their status."""
    try:
        pod_manager = get_pod_manager()
        pods = pod_manager.list_pods()

        # Gather config for each pod
        configs = {}
        for pod in pods:
            try:
                configs[pod.alias] = pod_manager.get_pod_config(pod.alias)
            except Exception:
                # Config might not exist for this pod
                configs[pod.alias] = {}

        display_pods_table(pods, configs)

    except Exception as e:
        handle_cli_error(e)


def show_command(alias: str) -> None:
    """Show detailed information about a pod."""
    try:
        pod_manager = get_pod_manager()
        scheduler = get_scheduler()

        # Get pod details
        pod = pod_manager.get_pod(alias)

        # Get any scheduled tasks for this pod
        scheduled_tasks = [
            t
            for t in scheduler.tasks
            if t.alias == alias and t.status.value == "pending"
        ]

        console.print(f"\n[bold cyan]Pod Details: {alias}[/bold cyan]")
        console.print("=" * 60)

        # Basic info
        console.print(f"[bold]ID:[/bold]        {pod.id}")
        console.print(f"[bold]Status:[/bold]    {pod.status.value.upper()}")

        # GPU info
        if pod.gpu_spec:
            console.print(f"[bold]GPU:[/bold]       {pod.gpu_spec}")
        else:
            console.print("[bold]GPU:[/bold]       [dim](unknown)[/dim]")

        # Storage info
        if pod.volume_gb:
            console.print(f"[bold]Storage:[/bold]   {pod.volume_gb}GB")
        else:
            console.print("[bold]Storage:[/bold]   [dim](unknown)[/dim]")

        if pod.container_disk_gb:
            console.print(f"[bold]Container:[/bold]  {pod.container_disk_gb}GB")

        # Cost info
        if pod.cost_per_hour:
            console.print(f"[bold]Cost:[/bold]      ${pod.cost_per_hour:.3f}/hour")
        else:
            console.print("[bold]Cost:[/bold]      [dim](unknown)[/dim]")

        # Network info (if running)
        if pod.ip_address and pod.ssh_port:
            console.print(f"[bold]IP:[/bold]        {pod.ip_address}:{pod.ssh_port}")

        # Image info
        if pod.image:
            # Truncate long image names
            image_display = (
                pod.image if len(pod.image) <= 50 else pod.image[:47] + "..."
            )
            console.print(f"[bold]Image:[/bold]     {image_display}")

        # Configuration
        config_values = pod_manager.get_pod_config(alias)
        if any(v is not None for v in config_values.values()):
            console.print("\n[bold cyan]Configuration:[/bold cyan]")
            for key, value in config_values.items():
                if value is not None:
                    console.print(f"  {key}: [bold]{value}[/bold]")

        # Scheduled tasks
        if scheduled_tasks:
            console.print("\n[bold yellow]Scheduled Tasks:[/bold yellow]")
            for task in scheduled_tasks:
                when_str = task.when_datetime.strftime("%Y-%m-%d %H:%M")
                console.print(
                    f"  • {task.action} at {when_str} [dim](id={task.id[:8]})[/dim]"
                )

        console.print("=" * 60 + "\n")

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

        # Clean completed/cancelled scheduled tasks
        scheduler = get_scheduler()
        removed_tasks = scheduler.clean_completed_tasks()

        if removed_tasks:
            console.print(
                f"🗑️  Removed [bold]{removed_tasks}[/bold] completed/cancelled scheduled task(s)."
            )

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


def template_create_command(
    identifier: str,
    alias_template: str,
    gpu: str,
    storage: str,
    container_disk: str | None = None,
    image: str | None = None,
    config: list[str] | None = None,
    force: bool = False,
) -> None:
    """Create a new pod template."""
    try:
        template_kwargs = {
            "identifier": identifier,
            "alias_template": alias_template,
            "gpu_spec": gpu,
            "storage_spec": storage,
        }

        # Add container disk if specified
        if container_disk is not None:
            template_kwargs["container_disk_spec"] = container_disk

        # Add image if specified
        if image is not None:
            template_kwargs["image"] = image

        # Parse config flags if provided
        if config:
            pod_config = parse_config_flags(config)
            template_kwargs["config"] = pod_config

        template = PodTemplate(**template_kwargs)  # type: ignore[arg-type]

        pod_manager = get_pod_manager()
        pod_manager.add_template(template, force)

        console.print(f"✅ Created template '[bold]{identifier}[/bold]'")
        console.print(f"   Alias template: {alias_template}")
        console.print(f"   GPU: {gpu}")
        console.print(f"   Storage: {storage}")
        if container_disk is not None:
            console.print(f"   Container disk: {container_disk}")
        if image is not None:
            console.print(f"   Image: {image}")
        if config:
            for config_item in config:
                console.print(f"   Config: {config_item}")

    except Exception as e:
        handle_cli_error(e)


def template_list_command() -> None:
    """List all pod templates."""
    try:
        pod_manager = get_pod_manager()
        templates = pod_manager.list_templates()

        if not templates:
            console.print("No templates found.")
            return

        from rich.table import Table

        table = Table(title="Pod Templates")
        table.add_column("Identifier", style="cyan", no_wrap=True)
        table.add_column("Alias Template", style="magenta")
        table.add_column("GPU", style="green")
        table.add_column("Storage", style="yellow")
        table.add_column("Container Disk", style="yellow")
        table.add_column("Image", style="blue")

        for template in templates:
            image_display = template.image if template.image else "(default)"
            container_disk_display = (
                template.container_disk_spec
                if template.container_disk_spec
                else "(default: 20GB)"
            )
            table.add_row(
                template.identifier,
                template.alias_template,
                template.gpu_spec,
                template.storage_spec,
                container_disk_display,
                image_display,
            )

        console.print(table)

    except Exception as e:
        handle_cli_error(e)


def template_delete_command(identifier: str, missing_ok: bool = False) -> None:
    """Delete a pod template."""
    try:
        pod_manager = get_pod_manager()
        template = pod_manager.remove_template(identifier, missing_ok)

        if template:
            console.print(f"✅ Deleted template '[bold]{identifier}[/bold]'")
        else:
            console.print(
                f"i  Template '[bold]{identifier}[/bold]' not found; nothing to do."
            )

    except Exception as e:
        handle_cli_error(e)


def cursor_command(alias: str, path: str | None = None) -> None:
    """Open Cursor editor with remote SSH connection to pod."""
    try:
        pod_manager = get_pod_manager()
        pod_manager.get_pod_id(alias)  # Validate alias exists

        import subprocess

        # Use configured default if path not provided
        if path is None:
            configured_path = pod_manager.get_pod_config_value(alias, "path")
            path = configured_path or "/workspace"

        remote_uri = f"vscode-remote://ssh-remote+{alias}{path}"
        console.print(f"🖥️  Opening Cursor at '[bold]{alias}:{path}[/bold]'…")

        subprocess.run(["cursor", "--folder-uri", remote_uri], check=True)
        console.print("✅ Cursor opened successfully.")

    except FileNotFoundError:
        console.print(
            "❌ Cursor command not found. Please ensure Cursor is installed and in your PATH.",
            style="red",
        )
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e)


def shell_command(alias: str) -> None:
    """Open an interactive SSH shell to the pod."""
    try:
        pod_manager = get_pod_manager()
        pod_manager.get_pod_id(alias)  # Validate alias exists

        import subprocess

        # Get configured path to cd into
        configured_path = pod_manager.get_pod_config_value(alias, "path")

        if configured_path:
            console.print(f"🐚 Connecting to '[bold]{alias}:{configured_path}[/bold]'…")
            # Use ssh -t to allocate a PTY for the cd command
            subprocess.run(
                ["ssh", "-A", "-t", alias, f"cd {configured_path} && exec bash -l"],
                check=False,
            )
        else:
            console.print(f"🐚 Connecting to '[bold]{alias}[/bold]'…")
            subprocess.run(["ssh", "-A", alias], check=False)

    except Exception as e:
        handle_cli_error(e)


def config_command(alias: str, args: list[str]) -> None:
    """Get or set configuration values for a pod.

    Usage:
        rp config <alias> <key>              # Get single value
        rp config <alias> key=value          # Set single value
        rp config <alias> key1=val1 key2=val2  # Set multiple values
    """
    try:
        pod_manager = get_pod_manager()
        valid_keys = ["path"]

        if not args:
            # No args - show error
            console.print(
                "❌ Usage: rp config <alias> <key> OR rp config <alias> key=value [key2=value2 ...]",
                style="red",
            )
            raise typer.Exit(1) from None

        # Check if any arg contains '=' (set mode) or all are plain keys (get mode)
        has_equals = any("=" in arg for arg in args)

        if has_equals:
            # Set mode: parse key=value pairs
            if any("=" not in arg for arg in args):
                console.print(
                    "❌ Cannot mix key=value and plain key arguments",
                    style="red",
                )
                raise typer.Exit(1) from None

            # Parse and validate all pairs first
            pairs = []
            for arg in args:
                if "=" not in arg:
                    continue
                key, _, value = arg.partition("=")
                key = key.strip()
                value = value.strip()

                if key not in valid_keys:
                    console.print(
                        f"❌ Invalid config key: {key}. Valid keys: {', '.join(valid_keys)}",
                        style="red",
                    )
                    raise typer.Exit(1) from None

                pairs.append((key, value if value else None))

            # Set all values and show feedback
            for key, value in pairs:
                old_value = pod_manager.get_pod_config_value(alias, key)
                pod_manager.set_pod_config(alias, key, value)

                if value is None:
                    console.print(f"✅ Cleared '{key}' for '[bold]{alias}[/bold]'")
                elif old_value is None:
                    console.print(
                        f"✅ Set '{key}' = '{value}' for '[bold]{alias}[/bold]' (new)"
                    )
                elif old_value != value:
                    console.print(
                        f"✅ Set '{key}' = '{value}' for '[bold]{alias}[/bold]' (was '{old_value}')"
                    )
                else:
                    console.print(
                        f"ℹ️  '{key}' already set to '{value}' for '[bold]{alias}[/bold]'"
                    )
        else:
            # Get mode: retrieve and display values
            if len(args) > 1:
                console.print(
                    "❌ To get multiple values, use: rp show <alias>",
                    style="red",
                )
                raise typer.Exit(1) from None

            key = args[0].strip()

            if key not in valid_keys:
                console.print(
                    f"❌ Invalid config key: {key}. Valid keys: {', '.join(valid_keys)}",
                    style="red",
                )
                raise typer.Exit(1) from None

            value = pod_manager.get_pod_config_value(alias, key)

            if value is None:
                console.print(f"{key}: [dim](not set)[/dim]")
            else:
                console.print(f"{key}: [bold]{value}[/bold]")

    except Exception as e:
        handle_cli_error(e)
