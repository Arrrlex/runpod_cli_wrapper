"""
CLI utilities and helper functions.

This module provides common functionality for the command-line interface,
including error handling, API setup, and output formatting.
"""

import contextlib
import getpass
import os
import subprocess
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from rp.config import API_KEY_FILE, LOCAL_SETUP_FILE, REMOTE_SETUP_FILE
from rp.core.models import GPUSpec, Pod, PodConfig, PodStatus, ScheduleTask
from rp.utils.api_client import RunPodAPIClient
from rp.utils.errors import RunPodCLIError

console = Console()


def setup_api_client() -> RunPodAPIClient:
    """Set up RunPod API client with authentication."""
    # Priority: env var, stored file, interactive prompt
    api_key = None

    if candidate := os.environ.get("RUNPOD_API_KEY"):
        api_key = candidate
    elif API_KEY_FILE.exists():
        api_key = API_KEY_FILE.read_text().strip()
    else:
        # Interactive prompt
        try:
            api_key = getpass.getpass("Enter RunPod API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\n❌ API key entry cancelled.", err=True)
            raise typer.Exit(1) from None

        if not api_key:
            typer.echo("❌ Empty API key provided.", err=True)
            raise typer.Exit(1)

        # Save for future use
        API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with API_KEY_FILE.open("w") as f:
            f.write(api_key + "\n")

        with contextlib.suppress(Exception):
            os.chmod(API_KEY_FILE, 0o600)

        console.print("🔐 Saved RunPod API key for future use.")

    return RunPodAPIClient(api_key)


def handle_cli_error(error: Exception) -> None:
    """Handle and display CLI errors appropriately."""
    if isinstance(error, RunPodCLIError):
        typer.echo(f"❌ {error.message}", err=True)
        if error.details:
            typer.echo(f"   {error.details}", err=True)
        raise typer.Exit(error.exit_code)
    else:
        typer.echo(f"❌ An unexpected error occurred: {error}", err=True)
        raise typer.Exit(1)


def parse_gpu_spec(gpu_string: str) -> GPUSpec:
    """Parse GPU specification from string like '2xA100' or 'h100' (defaults to 1)."""
    # First check for NxTYPE format
    if "x" in gpu_string.lower():
        parts = gpu_string.lower().split("x", 1)
        if len(parts) == 2 and parts[0].isdigit():
            count = int(parts[0])
            if count < 1:
                raise typer.BadParameter("GPU count must be >= 1") from None

            model = parts[1].strip().upper()
            if not model:
                raise typer.BadParameter("GPU type is missing, e.g. A100") from None

            return GPUSpec(count=count, model=model)
        else:
            # Contains 'x' but first part is not numeric - could be ambiguous
            # If it looks like it's trying to be NxTYPE format but invalid, raise error
            if len(parts) == 2 and len(parts[0]) == 1:
                raise typer.BadParameter(
                    "--gpu must be in the form NxTYPE (e.g. 2xA100) or just TYPE (e.g. h100)"
                ) from None

            # Otherwise treat as model name (e.g., "RTX4090")
            model = gpu_string.strip().upper()
            if not model:
                raise typer.BadParameter("GPU type cannot be empty") from None
            return GPUSpec(count=1, model=model)
    else:
        # No 'x', treat as GPU model without count (defaults to 1)
        model = gpu_string.strip().upper()
        if not model:
            raise typer.BadParameter("GPU type cannot be empty") from None
        return GPUSpec(count=1, model=model)


def parse_storage_spec(storage_string: str) -> int:
    """Parse storage specification from string like '500GB' into GB."""
    s = storage_string.upper().replace(" ", "")

    if s.endswith("GB"):
        num = s[:-2]
        factor = 1
    elif s.endswith("GIB"):
        num = s[:-3]
        factor = 1.074  # Convert GiB to GB
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

    gb = round(value * factor)
    if gb < 10:
        raise typer.BadParameter("--storage must be at least 10GB")

    return gb


def parse_config_flags(config_flags: list[str] | None) -> PodConfig:
    """Parse --config flags into a PodConfig object.

    Each flag should be in the format 'key=value', e.g., 'path=/workspace/project'.
    Supported keys: path
    """
    config = PodConfig()

    if not config_flags:
        return config

    for flag in config_flags:
        if "=" not in flag:
            raise typer.BadParameter(
                f"Invalid --config format: '{flag}'. Expected 'key=value' (e.g., 'path=/workspace/project')"
            )

        key, value = flag.split("=", 1)
        key = key.strip()
        value = value.strip()

        if key == "path":
            config.path = value if value else None
        else:
            raise typer.BadParameter(
                f"Unknown config key: '{key}'. Supported keys: path"
            )

    return config


def display_pods_table(pods: list[Pod]) -> None:
    """Display a table of pods."""
    if not pods:
        console.print(
            "[yellow]No aliases configured. Add one with: rp add <alias> <pod_id>[/yellow]"
        )
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Alias", style="green")
    table.add_column("ID", style="magenta")
    table.add_column("Status", style="white")

    for pod in pods:
        if pod.status == PodStatus.RUNNING:
            status_text = Text("running", style="bold green")
        elif pod.status == PodStatus.STOPPED:
            status_text = Text("stopped", style="yellow")
        else:
            status_text = Text("invalid", style="bold red")

        row = [pod.alias, pod.id, status_text]
        table.add_row(*row)

    console.print(table)


def display_schedule_table(tasks: list[ScheduleTask]) -> None:
    """Display a table of scheduled tasks."""
    if not tasks:
        console.print("[yellow]No scheduled tasks.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="magenta")
    table.add_column("Action", style="white")
    table.add_column("Alias", style="green")
    table.add_column("When (local)", style="white")
    table.add_column("Status", style="white")

    for task in tasks:
        when_local = task.when_datetime.strftime("%Y-%m-%d %H:%M %Z")

        if task.status.value == "pending":
            status_text = Text(task.status.value, style="bold green")
        elif task.status.value == "failed":
            status_text = Text(task.status.value, style="yellow")
        else:
            status_text = Text(task.status.value, style="dim")

        table.add_row(
            task.id,
            task.action,
            task.alias,
            when_local,
            status_text,
        )

    console.print(table)


def run_local_command(command_list: list[str], **env_vars) -> None:
    """Run a local command and handle errors."""
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        result = subprocess.run(
            command_list, check=True, capture_output=True, text=True, env=env_vars
        )
        if result.stdout:
            typer.echo(result.stdout.strip())
        if result.stderr:
            typer.echo(result.stderr.strip(), err=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"❌ Command failed with exit code {e.returncode}:", err=True)
        if e.stdout:
            typer.echo("--- STDOUT ---", err=True)
            typer.echo(e.stdout.strip(), err=True)
        if e.stderr:
            typer.echo("--- STDERR ---", err=True)
            typer.echo(e.stderr.strip(), err=True)

        from rp.utils.errors import SetupScriptError

        raise SetupScriptError.local_script_failed(e.returncode, e.stderr or "") from e


def run_local_command_stream(command_list: list[str]) -> None:
    """Run a local command and stream output live."""
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
                typer.echo(line.rstrip())
            returncode = proc.wait()
            if returncode != 0:
                from rp.utils.errors import SetupScriptError

                raise SetupScriptError.local_script_failed(
                    returncode, "See output above"
                )
    except FileNotFoundError as e:
        typer.echo(f"❌ Command not found: {command_list[0]} ({e})", err=True)
        raise typer.Exit(1) from e


def run_setup_scripts(alias: str) -> None:
    """Run local and remote setup scripts if they exist."""
    if LOCAL_SETUP_FILE.exists():
        console.print("⚙️  Running local setup…")
        run_local_command(
            ["bash", str(LOCAL_SETUP_FILE)],
            POD_HOST=alias,
        )

    if REMOTE_SETUP_FILE.exists():
        console.print("⚙️  Running remote setup via scp…")
        remote_setup_script = REMOTE_SETUP_FILE.read_text()

        # Use temp file for safety
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".sh", prefix="setup_pod_"
        ) as temp_script:
            temp_script.write(remote_setup_script)
            local_script_path = Path(temp_script.name)

        try:
            remote_script_path = "/tmp/setup_pod.sh"

            console.print("    2. Copying setup script to pod…")
            run_local_command(
                [
                    "scp",
                    "-o",
                    "StrictHostKeyChecking=no",
                    str(local_script_path),
                    f"{alias}:{remote_script_path}",
                ]
            )

            console.print("    3. Making script executable…")
            run_local_command(["ssh", alias, f"chmod +x {remote_script_path}"])

            console.print("    4. Executing setup script on pod…")
            run_local_command_stream(["ssh", "-A", alias, remote_script_path])

            console.print("✅ Remote setup complete.")

        finally:
            # Cleanup temp file
            local_script_path.unlink()
