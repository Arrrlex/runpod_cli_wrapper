"""
Main entry point for the RunPod CLI wrapper.

This module provides the main application entry point and command-line interface
for the RunPod CLI wrapper. It includes commands for managing aliases, pods,
and scheduling tasks.
"""

import contextlib
import getpass
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import runpod
import typer
from dateutil import tz
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from runpod_cli_wrapper.config import (
    API_KEY_FILE,
    LOCAL_SETUP_FILE,
    REMOTE_SETUP_FILE,
    SSH_CONFIG_FILE,
)
from runpod_cli_wrapper.scheduling import (
    auto_clear_completed_tasks,
    ensure_launchd_scheduler_installed,
    load_schedule_tasks,
    now_local,
    parse_duration_to_seconds,
    parse_schedule_at,
    save_schedule_tasks,
    schedule_task_stop,
    to_epoch_seconds,
)
from runpod_cli_wrapper.ssh_config import (
    ensure_config_dir_exists,
    load_pod_configs,
    prune_rp_managed_blocks,
    remove_ssh_host_block,
    save_pod_configs,
    update_ssh_config,
    validate_host_alias,
)

app = typer.Typer(help="RunPod utility for starting and stopping pods")
console = Console()
schedule_app = typer.Typer(help="Manage scheduled tasks")

# Default image for PyTorch 2.8 runtime
DEFAULT_PYTORCH28_IMAGE = (
    "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"
)


def setup_runpod_api():
    """Ensure RunPod API key is available.

    Priority:
      1) RUNPOD_API_KEY env var
      2) Stored key at API_KEY_FILE
      3) Prompt user (hidden input) and store it for future use
    """
    if candidate := os.environ.get("RUNPOD_API_KEY"):
        api_key = candidate
    elif API_KEY_FILE.exists():
        api_key = API_KEY_FILE.read_text().strip()
    else:
        # Interactive prompt (hidden input)
        try:
            api_key = getpass.getpass("Enter RunPod API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n❌ API key entry cancelled.", err=True)
            raise typer.Exit(1) from None
        if not api_key:
            typer.echo("❌ Empty API key provided.", err=True)
            raise typer.Exit(1)
        # Persist with restricted permissions
        ensure_config_dir_exists()
        with API_KEY_FILE.open("w") as f:
            f.write(api_key + "\n")
        with contextlib.suppress(Exception):
            # Best-effort; ignore if filesystem doesn't support chmod
            os.chmod(API_KEY_FILE, 0o600)
        console.print("🔐 Saved RunPod API key for future use.")
    runpod.api_key = api_key


def determine_pod_status(pod_id: str) -> str:
    """Return a coarse status for a pod_id: 'running', 'stopped', or 'invalid'.

    - running: desiredStatus == 'RUNNING'
    - stopped: any other known state
    - invalid: errors fetching pod or response missing expected fields
    """
    try:
        pod = runpod.get_pod(pod_id)
    except Exception:
        return "invalid"

    if not isinstance(pod, dict) or not pod.get("id"):
        return "invalid"

    desired = str(pod.get("desiredStatus") or "").upper()
    if desired == "RUNNING":
        return "running"
    if desired == "EXITED":
        return "stopped"
    # Map any other valid, known-but-not-running state to 'stopped' for simplicity
    return "stopped"


def run_local_command(command_list, **env_vars):
    """Runs a local command, waits for it to complete, and prints the result."""
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        result = subprocess.run(
            command_list, check=True, capture_output=True, text=True, env=env_vars
        )
        if result.stdout:
            typer.echo(result.stdout.strip())
        if result.stderr:
            # Print stderr to the standard error stream
            typer.echo(result.stderr.strip(), err=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"❌ Command failed with exit code {e.returncode}:", err=True)
        if e.stdout:
            typer.echo("--- STDOUT ---", err=True)
            typer.echo(e.stdout.strip(), err=True)
        if e.stderr:
            typer.echo("--- STDERR ---", err=True)
            typer.echo(e.stderr.strip(), err=True)
        raise typer.Exit(1) from e


def run_local_command_stream(command_list):
    """Run a local command and stream its combined stdout/stderr live."""
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        with subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Print each line as it arrives
                typer.echo(line.rstrip())
            returncode = proc.wait()
            if returncode != 0:
                typer.echo(f"❌ Command failed with exit code {returncode}", err=True)
                raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"❌ Command not found: {command_list[0]} ({e})", err=True)
        raise typer.Exit(1) from e


def _parse_gpu_arg(text: str) -> tuple[int, str]:
    """Parse --gpu like '2xA100' → (2, 'A100')."""
    s = (text or "").strip()
    if not s:
        raise typer.BadParameter("--gpu must be provided, e.g. 2xA100")
    parts = s.lower().split("x", 1)
    if len(parts) != 2 or not parts[0].isdigit():
        raise typer.BadParameter("--gpu must be in the form NxTYPE, e.g. 2xA100")
    count = int(parts[0])
    if count < 1:
        raise typer.BadParameter("GPU count must be >= 1")
    model_key = parts[1].strip().upper()
    if not model_key:
        raise typer.BadParameter("GPU type is missing, e.g. A100")
    return count, model_key


def _parse_storage_arg(text: str) -> int:
    """Parse --storage like '500GB' or '1TB' → integer GB."""
    s = (text or "").strip().upper().replace(" ", "")
    if not s:
        raise typer.BadParameter("--storage must be provided, e.g. 500GB")
    if s.endswith("GB"):
        num = s[:-2]
        factor = 1
    elif s.endswith("GIB"):
        num = s[:-3]
        # Convert GiB to GB approximately
        factor = 1.074
    elif s.endswith("TB"):
        num = s[:-2]
        factor = 1000
    elif s.endswith("TIB"):
        num = s[:-3]
        factor = 1024
    else:
        raise typer.BadParameter("--storage must end with GB/GiB/TB/TiB, e.g. 500GB")
    try:
        value = float(num)
    except ValueError:
        raise typer.BadParameter("--storage numeric part is invalid") from None
    gb = int(round(value * factor))
    if gb < 10:
        raise typer.BadParameter("--storage must be at least 10GB")
    return gb


def _resolve_gpu_type_id(model_key: str) -> str:
    """Return a RunPod GPU type id matching model_key (e.g., 'A100'), preferring highest VRAM."""
    try:
        gpus = runpod.get_gpus()
    except Exception as e:
        typer.echo(f"❌ Failed to list GPUs via SDK: {e}", err=True)
        raise typer.Exit(1) from e

    if not isinstance(gpus, list | tuple):
        if isinstance(gpus, dict) and isinstance(gpus.get("gpus"), list):
            gpus = gpus["gpus"]
        else:
            typer.echo("❌ Unexpected get_gpus() payload shape.", err=True)
            raise typer.Exit(1)

    model_upper = model_key.upper()
    candidates: list[tuple[float, str]] = []
    for item in gpus:
        ident = str(item.get("id", ""))
        name = str(item.get("displayName", ""))
        mem = item.get("memoryInGb")
        if model_upper in ident.upper() or model_upper in name.upper():
            try:
                mem_val = float(mem) if mem is not None else 0.0
            except Exception:
                mem_val = 0.0
            candidates.append((mem_val, ident))

    if not candidates:
        typer.echo(
            f"❌ Could not find GPU type matching '{model_key}'. Try a different value (e.g., A100, H100, L40S).",
            err=True,
        )
        raise typer.Exit(1)

    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def _wait_for_pod_ready(pod_id: str) -> dict:
    """Wait for a newly-created pod to be RUNNING with network info, return pod details."""
    console.print("⏱️ Waiting for pod to be fully ready…")
    pod_details = None
    # Wait up to 10 minutes
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Waiting for network…", total=None)
        for i in range(120):  # 120 * 5s = 10 minutes
            pod_details = runpod.get_pod(pod_id)
            if pod_details and pod_details.get("runtime") is not None:
                progress.update(
                    task, description="Pod is RUNNING and network is active"
                )
                break
            progress.update(task, description=f"Waiting… (attempt {i + 1}/120)")
            time.sleep(5)
        else:
            typer.echo("❌ Timed out waiting for pod to become ready.", err=True)
            raise typer.Exit(1)
    console.print(
        "✅ Pod is now [bold green]RUNNING[/bold green] and network is active."
    )
    return pod_details


def stop_impl(host_alias: str) -> None:
    """Shared implementation to stop a pod and clean SSH block. Requires runpod to be set up."""
    pod_id = validate_host_alias(host_alias)

    console.print(
        f"🛑 Stopping RunPod pod: [bold]{pod_id}[/bold] (alias: {host_alias})…"
    )
    try:
        runpod.stop_pod(pod_id)
        typer.echo(f"✅ Pod {pod_id} has been stopped.")
        removed = remove_ssh_host_block(host_alias)
        if removed:
            console.print(
                f"🧹 Removed SSH config block for '[bold]{host_alias}[/bold]'"
            )
    except Exception as e:
        # Check if the pod was already stopped
        try:
            pod = runpod.get_pod(pod_id)
            if pod.get("desiredStatus") == "EXITED":
                console.print(f"✅ Pod {pod_id} was already stopped.")
                removed = remove_ssh_host_block(host_alias)
                if removed:
                    console.print(
                        f"🧹 Removed SSH config block for '[bold]{host_alias}[/bold]'"
                    )
            else:
                typer.echo(f"❌ An unexpected error occurred: {e}", err=True)
                raise typer.Exit(1)
        except Exception as get_e:
            typer.echo(
                f"❌ An error occurred while stopping and checking the pod: {get_e}",
                err=True,
            )
            raise typer.Exit(1) from None


def destroy_impl(host_alias: str) -> None:
    """Terminate a pod and remove its alias and SSH config block.

    - If pod is running, stop it first (best-effort)
    - Terminate the pod
    - Remove SSH config block for the alias
    - Remove alias from local config
    """
    pod_id = validate_host_alias(host_alias)

    console.print(
        f"🔥 Destroying RunPod pod: [bold]{pod_id}[/bold] (alias: {host_alias})…"
    )

    # Best-effort stop first, but proceed on failure
    with contextlib.suppress(Exception):
        status = determine_pod_status(pod_id)
        if status == "running":
            console.print("⏹️  Pod is running; stopping before termination…")
            runpod.stop_pod(pod_id)

    # Terminate
    try:
        runpod.terminate_pod(pod_id)
        console.print(f"✅ Terminated pod [bold]{pod_id}[/bold].")
    except Exception as e:
        reason = (str(e) or e.__class__.__name__).strip()
        typer.echo(f"❌ Failed to terminate pod: {reason}", err=True)
        raise typer.Exit(1) from e

    # Clean SSH config
    removed = remove_ssh_host_block(host_alias)
    if removed:
        console.print(f"🧹 Removed SSH config block for '[bold]{host_alias}[/bold]'")

    # Remove alias mapping
    pod_configs = load_pod_configs()
    if host_alias in pod_configs:
        _ = pod_configs.pop(host_alias, None)
        save_pod_configs(pod_configs)
        console.print(
            f"🗑️  Removed alias '[bold]{host_alias}[/bold]' from local configuration."
        )


@app.command()
def destroy(
    host_alias: str = typer.Argument(
        ..., help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)"
    ),
):
    """Terminate a pod, remove SSH config, and delete the alias mapping."""
    setup_runpod_api()
    destroy_impl(host_alias)


@app.command()
def add(
    alias: str = typer.Argument(..., help="Alias name to assign to a RunPod pod id"),
    pod_id: str = typer.Argument(..., help="RunPod pod id (e.g., 89qgenjznh5t2j)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite if alias already exists"
    ),
):
    """Add or update an alias → pod id mapping."""
    pod_configs = load_pod_configs()
    if alias in pod_configs and not force:
        typer.echo(
            f"❌ Alias '{alias}' already exists. Use --force to overwrite.", err=True
        )
        raise typer.Exit(1)
    pod_configs[alias] = pod_id
    save_pod_configs(pod_configs)
    typer.echo(f"✅ Saved alias '{alias}' -> {pod_id}")


@app.command()
def delete(
    alias: str = typer.Argument(..., help="Alias name to remove"),
    missing_ok: bool = typer.Option(
        False, "--missing-ok", help="Do not error if alias is missing"
    ),
):
    """Delete an alias mapping."""
    pod_configs = load_pod_configs()
    if alias not in pod_configs:
        if missing_ok:
            typer.echo(f"i  Alias '{alias}' not found; nothing to do.")
            return
        typer.echo(f"❌ Alias '{alias}' not found.", err=True)
        raise typer.Exit(1)
    value = pod_configs.pop(alias)
    save_pod_configs(pod_configs)
    typer.echo(f"✅ Removed alias '{alias}' (was {value})")


def _resume_pod_and_wait(pod_id: str, host_alias: str) -> dict:
    """Resume pod and wait for it to be ready. Returns pod details."""
    console.print(
        f"🚀 Resuming RunPod pod: [bold]{pod_id}[/bold] (alias: {host_alias})…"
    )
    try:
        runpod.resume_pod(pod_id, gpu_count=1)
    except Exception as e:
        # Check if the pod is already running, which is not an error
        pod = runpod.get_pod(pod_id)
        if pod.get("desiredStatus") != "RUNNING":
            reason = (str(e) or e.__class__.__name__).strip()
            typer.echo(f"❌ Failed to resume pod: {reason}", err=True)
            raise typer.Exit(1) from e
        else:
            console.print("[yellow]Pod was already running. Proceeding…[/yellow]")

    console.print("⏱️ Waiting for pod to be fully ready…")
    pod_details = None
    # Wait for up to 2 minutes for the pod to be ready
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Waiting for network…", total=None)
        for i in range(24):
            pod_details = runpod.get_pod(pod_id)
            if pod_details and pod_details.get("runtime") is not None:
                progress.update(
                    task, description="Pod is RUNNING and network is active"
                )
                break
            progress.update(task, description=f"Waiting… (attempt {i + 1}/24)")
            time.sleep(5)
        else:
            typer.echo("❌ Timed out waiting for pod to become ready.", err=True)
            raise typer.Exit(1) from None
    console.print(
        "✅ Pod is now [bold green]RUNNING[/bold green] and network is active."
    )
    return pod_details


def _extract_ssh_info(pod_details: dict) -> tuple[str, int]:
    """Extract IP address and port from pod details."""
    ip_address = None
    port_number = None
    # Find the public SSH port from the pod details
    for port in pod_details.get("runtime", {}).get("ports", []):
        if port.get("privatePort") == 22 and port.get("isIpPublic") is True:
            ip_address = port["ip"]
            port_number = port["publicPort"]
            break

    if not ip_address or not port_number:
        typer.echo("❌ Could not find public SSH port information.", err=True)
        raise typer.Exit(1) from None

    console.print(f"Found IP: [bold]{ip_address}[/bold]")
    console.print(f"Found Port: [bold]{port_number}[/bold]")
    return ip_address, port_number


def _run_setup_scripts(host_alias: str) -> None:
    """Run local and remote setup scripts if they exist."""
    if LOCAL_SETUP_FILE.exists():
        console.print("⚙️  Running local setup…")
        # Run the inline shell script through bash so that shell expansions (e.g. ~) work.
        run_local_command(
            ["bash", str(LOCAL_SETUP_FILE)],
            POD_HOST=host_alias,
        )

    if REMOTE_SETUP_FILE.exists():
        console.print("⚙️  Running remote setup via scp…")
        remote_setup_script = REMOTE_SETUP_FILE.read_text()
        # Use a temporary file to safely handle the script content
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".sh", prefix="setup_pod_"
        ) as temp_script:
            temp_script.write(remote_setup_script)
            local_script_path = Path(temp_script.name)
        remote_script_path = "/tmp/setup_pod.sh"
        console.print("    2. Copying setup script to pod…")
        run_local_command(
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                str(local_script_path),
                f"{host_alias}:{remote_script_path}",
            ]
        )

        console.print("    3. Making script executable…")
        run_local_command(["ssh", host_alias, f"chmod +x {remote_script_path}"])

        console.print("    4. Executing setup script on pod (this may take a minute)…")
        # Stream the remote setup script output live for better feedback
        run_local_command_stream(["ssh", host_alias, remote_script_path])

        # Clean up the local temporary script file
        local_script_path.unlink()
        console.print("✅ Remote setup complete.")


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
    setup_runpod_api()

    # Guard alias existence
    pod_configs = load_pod_configs()
    if alias in pod_configs and not force:
        typer.echo(
            f"❌ Alias '{alias}' already exists. Use --force to overwrite.", err=True
        )
        raise typer.Exit(1)

    # Parse inputs
    try:
        gpu_count, model_key = _parse_gpu_arg(gpu)
        volume_gb = _parse_storage_arg(storage)
    except typer.BadParameter as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e

    gpu_type_id = _resolve_gpu_type_id(model_key)

    console.print(
        f"🚀 Creating pod '[bold]{alias}[/bold]': image=[dim]{DEFAULT_PYTORCH28_IMAGE}[/dim], GPU={gpu_count}x{model_key} (id={gpu_type_id}), volume={volume_gb}GB"
    )

    if dry_run:
        console.print("[bold]DRY RUN[/bold] No changes were made.")
        return

    try:
        created = runpod.create_pod(
            name=alias,
            image_name=DEFAULT_PYTORCH28_IMAGE,
            gpu_type_id=gpu_type_id,
            gpu_count=gpu_count,
            volume_in_gb=volume_gb,
            support_public_ip=True,
            start_ssh=True,
            ports="22/tcp,8888/http",
        )
    except Exception as e:
        reason = (str(e) or e.__class__.__name__).strip()
        typer.echo(f"❌ Failed to create pod: {reason}", err=True)
        raise typer.Exit(1) from e

    pod_id = created.get("id") if isinstance(created, dict) else None
    if not pod_id:
        typer.echo("❌ Could not determine created pod id from SDK response.", err=True)
        raise typer.Exit(1)

    # Persist alias → pod id mapping
    pod_configs[alias] = pod_id
    save_pod_configs(pod_configs)
    console.print(f"✅ Saved alias '[bold]{alias}[/bold]' -> {pod_id}")

    # Wait until ready and configure SSH
    pod_details = _wait_for_pod_ready(pod_id)
    ip_address, port_number = _extract_ssh_info(pod_details)

    console.print(f"📝 Updating local SSH config file: [dim]{SSH_CONFIG_FILE}[/dim]")
    update_ssh_config(alias, pod_id, ip_address, port_number)
    console.print("✅ Local SSH config updated successfully.")

    # Run setup scripts
    _run_setup_scripts(alias)


@app.command()
def start(
    host_alias: str = typer.Argument(
        ..., help="SSH host alias for the pod (e.g., runpod-1, local-saes-1)"
    ),
):
    """Start and configure a RunPod instance."""
    setup_runpod_api()
    pod_id = validate_host_alias(host_alias)

    pod_details = _resume_pod_and_wait(pod_id, host_alias)
    ip_address, port_number = _extract_ssh_info(pod_details)

    console.print(f"📝 Updating local SSH config file: [dim]{SSH_CONFIG_FILE}[/dim]")
    update_ssh_config(host_alias, pod_id, ip_address, port_number)
    console.print("✅ Local SSH config updated successfully.")

    _run_setup_scripts(host_alias)


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
    # Validate alias early to ensure we don't schedule for unknown host
    _ = validate_host_alias(host_alias)

    if schedule_at and schedule_in:
        typer.echo(
            "❌ --schedule-at and --schedule-in are mutually exclusive", err=True
        )
        raise typer.Exit(1)

    if schedule_at or schedule_in:
        try:
            if schedule_at:
                when_dt = parse_schedule_at(schedule_at)
            else:
                seconds = parse_duration_to_seconds(schedule_in or "")
                when_dt = now_local() + timedelta(seconds=seconds)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e

        local_str = when_dt.strftime("%Y-%m-%d %H:%M %Z")
        rel_seconds = max(0, to_epoch_seconds(when_dt) - to_epoch_seconds(now_local()))
        rel_desc = (
            f"in {rel_seconds // 3600}h{(rel_seconds % 3600) // 60:02d}m"
            if rel_seconds >= 60
            else f"in {rel_seconds}s"
        )

        if dry_run:
            console.print(
                f"⏰ [bold]DRY RUN[/bold] Would schedule stop of '[bold]{host_alias}[/bold]' at {local_str} ({rel_desc})."
            )
            return

        task = schedule_task_stop(host_alias, when_dt)
        console.print(
            f"⏰ Scheduled stop of '[bold]{host_alias}[/bold]' at [bold]{local_str}[/bold] ({rel_desc}). [dim](id={task['id']})[/dim]"
        )
        # Ensure the scheduler is set up on macOS
        ensure_launchd_scheduler_installed(console)
        return

    if dry_run:
        console.print(
            f"[bold]DRY RUN[/bold] Would stop '[bold]{host_alias}[/bold]' now."
        )
        return

    setup_runpod_api()
    stop_impl(host_alias)


@app.command("list")
def list_aliases():
    """List all aliases as a table: Alias, ID, Status (running, stopped, invalid)."""
    pod_configs = load_pod_configs()
    if not pod_configs:
        console.print(
            "[yellow]No aliases configured. Add one with: rp add <alias> <pod_id>[/yellow]"
        )
        return

    # Require API key to compute statuses
    setup_runpod_api()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Alias", style="green")
    table.add_column("ID", style="magenta")
    table.add_column("Status", style="white")

    for alias, pod_id in sorted(pod_configs.items()):
        status = determine_pod_status(pod_id)
        if status == "running":
            status_text = Text("running", style="bold green")
        elif status == "stopped":
            status_text = Text("stopped", style="yellow")
        else:
            status_text = Text("invalid", style="bold red")
        table.add_row(alias, pod_id, status_text)

    console.print(table)


@app.command()
def clean():
    """Remove invalid aliases and prune rp-managed SSH blocks no longer valid.

    - Removes aliases from pods.json where status == invalid
    - Removes any rp-managed SSH blocks whose alias is not present in pods.json
    - Removes any rp-managed SSH blocks whose status == invalid
    """
    pod_configs = load_pod_configs()
    if not pod_configs:
        typer.echo("No aliases configured. Nothing to clean.")
        return
    setup_runpod_api()

    to_remove: list[str] = []
    for alias, pod_id in sorted(pod_configs.items()):
        status = determine_pod_status(pod_id)
        if status == "invalid":
            to_remove.append(alias)

    if not to_remove:
        typer.echo("✅ No invalid aliases found.")
        return

    for alias in to_remove:
        removed_id = pod_configs.pop(alias, None)
        console.print(
            f"🧹 Removing invalid alias '[bold]{alias}[/bold]' (was {removed_id})"
        )

    save_pod_configs(pod_configs)
    console.print(f"✅ Removed [bold]{len(to_remove)}[/bold] invalid alias(es).")

    # Prune rp-managed SSH blocks for aliases no longer tracked
    valid_aliases = set(pod_configs.keys())
    removed_blocks_dangling = prune_rp_managed_blocks(valid_aliases)

    # Prune rp-managed blocks for aliases that still exist but are invalid
    removed_blocks_invalid = 0
    for alias, pod_id in pod_configs.items():
        if determine_pod_status(pod_id) == "invalid":
            removed_blocks_invalid += remove_ssh_host_block(alias)

    total_removed = removed_blocks_dangling + removed_blocks_invalid
    if total_removed:
        console.print(
            f"🧹 Removed [bold]{total_removed}[/bold] rp-managed SSH config block(s) (dangling={removed_blocks_dangling}, invalid={removed_blocks_invalid})."
        )
    else:
        console.print("✅ No rp-managed SSH config blocks to prune.")


@app.command("scheduler-tick")
def scheduler_tick():
    """Execute due scheduled tasks (intended to be run by launchd every minute)."""
    tasks = load_schedule_tasks()
    if not tasks:
        return
    now_epoch = int(datetime.now(UTC).timestamp())
    changed = False

    # Initialize API once if we have any due tasks
    any_due = any(
        t.get("status") == "pending" and int(t.get("when_epoch", 0)) <= now_epoch
        for t in tasks
    )
    if any_due:
        try:
            setup_runpod_api()
        except Exception:
            # Could not init API; defer tasks
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
        save_schedule_tasks(tasks)


@schedule_app.command("list")
def schedule_list():
    """List scheduled tasks."""
    tasks = load_schedule_tasks()
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
            datetime.fromtimestamp(when_epoch, tz.tzlocal()).strftime(
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
def schedule_cancel(task_id: str = typer.Argument(..., help="Task id to cancel")):
    """Cancel a scheduled task by id (sets status to 'cancelled')."""
    tasks = load_schedule_tasks()
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
    save_schedule_tasks(tasks)
    console.print(f"✅ Cancelled task [bold]{task_id}[/bold].")


@schedule_app.command("clear-completed")
def schedule_clear_completed():
    """Remove tasks with status 'completed'."""
    removed = auto_clear_completed_tasks()
    if removed:
        console.print(f"✅ Removed [bold]{removed}[/bold] completed task(s).")
    else:
        console.print("No completed tasks to remove.")


def main():
    # Auto-clear completed tasks before any command runs to keep schedule tidy
    with contextlib.suppress(Exception):
        # Ignore cleanup errors; should never block commands
        removed = auto_clear_completed_tasks()
        if removed:
            # Keep this silent in normal output; uncomment to log cleanup
            pass
    # Mount schedule sub-app
    app.add_typer(schedule_app, name="schedule")
    app()


if __name__ == "__main__":
    main()
