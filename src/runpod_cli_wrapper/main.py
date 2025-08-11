import contextlib
import getpass
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import runpod
import typer
from dateutil import parser as date_parser
from dateutil import tz
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

# --- CONFIGURATION ---
# Location to store alias→pod_id mappings
CONFIG_DIR = Path.home() / ".config" / "rp"
POD_CONFIG_FILE = CONFIG_DIR / "pods.json"
API_KEY_FILE = CONFIG_DIR / "runpod_api_key"
REMOTE_SETUP_FILE = CONFIG_DIR / "setup_remote.sh"
LOCAL_SETUP_FILE = CONFIG_DIR / "setup_local.sh"

# The full path to your SSH config file.
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

# --- END CONFIGURATION ---

# Scheduler storage and macOS launchd integration
SCHEDULE_FILE = CONFIG_DIR / "schedule.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABEL = "com.rp.scheduler"
LAUNCHD_PLIST = LAUNCH_AGENTS_DIR / f"{LAUNCHD_LABEL}.plist"
LOGS_DIR = Path.home() / "Library" / "Logs"
SCHEDULER_LOG_FILE = LOGS_DIR / "rp-scheduler.log"

app = typer.Typer(help="RunPod utility for starting and stopping pods")
console = Console()
schedule_app = typer.Typer(help="Manage scheduled tasks")


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


def validate_host_alias(host_alias: str) -> str:
    """Validate that the host alias exists in the stored configuration and return the pod id."""
    pod_configs = load_pod_configs()
    if host_alias not in pod_configs:
        typer.echo(f"❌ Unknown host alias: {host_alias}", err=True)
        if pod_configs:
            typer.echo("Available aliases:", err=True)
            for alias in pod_configs:
                typer.echo(f"  {alias}", err=True)
        else:
            typer.echo(
                "No aliases configured. Add one with: rp add <alias> <pod_id>", err=True
            )
        raise typer.Exit(1)
    return pod_configs[host_alias]


def ensure_config_dir_exists() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_pod_configs() -> dict:
    """Load alias→pod_id mappings from POD_CONFIG_FILE; return empty dict if missing or invalid."""
    try:
        with POD_CONFIG_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        typer.echo(f"⚠️  Config file is not valid JSON: {POD_CONFIG_FILE}", err=True)
        return {}


def save_pod_configs(pod_configs: dict) -> None:
    ensure_config_dir_exists()
    with POD_CONFIG_FILE.open("w") as f:
        json.dump(pod_configs, f, indent=2, sort_keys=True)
        f.write("\n")


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


# --- SSH CONFIG HELPERS ---
MARKER_PREFIX = "# rp:managed"


def _build_marker(alias: str, pod_id: str) -> str:
    from datetime import datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"    {MARKER_PREFIX} alias={alias} pod_id={pod_id} updated={ts}\n"


def _load_ssh_config_lines() -> list[str]:
    try:
        with SSH_CONFIG_FILE.open("r") as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _write_ssh_config_lines(lines: list[str]) -> None:
    SSH_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SSH_CONFIG_FILE.open("w") as f:
        f.writelines(lines)


def _parse_ssh_blocks(lines: list[str]) -> list[dict]:
    """Parse SSH config into blocks. Each block starts with a 'Host ' line.

    Returns list of dicts with keys: start, end (exclusive), hosts, managed, marker_index.
    """
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*Host\s+(.+)$", line)
        if m:
            start = i
            # Collect until next Host or EOF
            i += 1
            while i < len(lines) and not re.match(r"^\s*Host\s+", lines[i]):
                i += 1
            end = i
            host_names = m.group(1).strip().split()
            managed = False
            marker_index = -1
            for j in range(start + 1, end):
                if lines[j].lstrip().startswith(MARKER_PREFIX):
                    managed = True
                    marker_index = j
                    break
            blocks.append(
                {
                    "start": start,
                    "end": end,
                    "hosts": host_names,
                    "managed": managed,
                    "marker_index": marker_index,
                }
            )
        else:
            i += 1
    return blocks


def remove_ssh_host_block(alias: str) -> int:
    """Remove rp-managed Host blocks that include the given alias. Returns count removed."""
    lines = _load_ssh_config_lines()
    if not lines:
        return 0
    blocks = _parse_ssh_blocks(lines)
    to_delete_ranges: list[tuple[int, int]] = []
    for blk in blocks:
        if blk["managed"] and alias in blk["hosts"]:
            to_delete_ranges.append((blk["start"], blk["end"]))
    if not to_delete_ranges:
        return 0
    # Build new lines skipping deleted ranges
    new_lines: list[str] = []
    cur = 0
    for start, end in to_delete_ranges:
        new_lines.extend(lines[cur:start])
        cur = end
    new_lines.extend(lines[cur:])
    _write_ssh_config_lines(new_lines)
    return len(to_delete_ranges)


def prune_rp_managed_blocks(valid_aliases: set[str]) -> int:
    """Remove rp-managed blocks whose alias is not in valid_aliases. Returns count removed."""
    lines = _load_ssh_config_lines()
    if not lines:
        return 0
    blocks = _parse_ssh_blocks(lines)
    to_delete_ranges: list[tuple[int, int]] = []
    for blk in blocks:
        if not blk["managed"]:
            continue
        # If any alias in the block is not valid, and block is rp-managed, delete it.
        # Prefer strict match: delete if none of the hosts are in valid_aliases.
        if not any(h in valid_aliases for h in blk["hosts"]):
            to_delete_ranges.append((blk["start"], blk["end"]))
    if not to_delete_ranges:
        return 0
    new_lines: list[str] = []
    cur = 0
    for start, end in to_delete_ranges:
        new_lines.extend(lines[cur:start])
        cur = end
    new_lines.extend(lines[cur:])
    _write_ssh_config_lines(new_lines)
    return len(to_delete_ranges)


def update_ssh_config(
    host_alias: str, pod_id: str, new_hostname: str, new_port: int | str
) -> None:
    """Create or update a Host block for alias with rp marker, HostName and Port."""
    lines = _load_ssh_config_lines()
    blocks = _parse_ssh_blocks(lines)

    # Prepare updated block content
    new_block: list[str] = []
    new_block.append(f"Host {host_alias}\n")
    new_block.append(_build_marker(host_alias, pod_id))
    new_block.append(f"    HostName {new_hostname}\n")
    new_block.append("    User root\n")
    new_block.append(f"    Port {new_port}\n")
    new_block.append("    IdentitiesOnly yes\n")
    new_block.append("    IdentityFile ~/.ssh/runpod\n")

    # Try to find an existing block for this alias
    target_block = None
    for blk in blocks:
        if host_alias in blk["hosts"]:
            target_block = blk
            break

    if target_block is None:
        # Append with a separating newline if needed
        if lines and lines[-1].strip() != "":
            lines.append("\n")
        lines.extend(new_block)
        _write_ssh_config_lines(lines)
        return

    # Replace the existing block entirely to ensure marker and fields are correct
    start, end = target_block["start"], target_block["end"]
    new_lines = []
    new_lines.extend(lines[:start])
    new_lines.extend(new_block)
    new_lines.extend(lines[end:])
    _write_ssh_config_lines(new_lines)


def run_local_command(command_list):
    """Runs a local command, waits for it to complete, and prints the result."""
    typer.echo(f"-> Running: {' '.join(command_list)}")
    try:
        result = subprocess.run(
            command_list, check=True, capture_output=True, text=True
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


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_schedule_tasks() -> list[dict]:
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


def _save_schedule_tasks(tasks: list[dict]) -> None:
    _ensure_config_dir()
    tmp_path = SCHEDULE_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(tasks, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(SCHEDULE_FILE)


def _auto_clear_completed_tasks() -> int:
    """Remove tasks with status 'completed' and persist if any were removed. Returns count removed."""
    tasks = _load_schedule_tasks()
    before = len(tasks)
    if before == 0:
        return 0
    pending_or_failed = [t for t in tasks if t.get("status") != "completed"]
    removed = before - len(pending_or_failed)
    if removed > 0:
        _save_schedule_tasks(pending_or_failed)
    return removed


def _now_local() -> datetime:
    return datetime.now(tz.tzlocal())


def _to_epoch_seconds(dt: datetime) -> int:
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
    now = now or _now_local()

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
    tasks = _load_schedule_tasks()
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


def _ensure_launchd_scheduler_installed() -> None:
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
        local_setup_script = LOCAL_SETUP_FILE.read_text().format(host=host_alias)
        run_local_command(["bash", "-lc", local_setup_script])

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
                when_dt = _now_local() + timedelta(seconds=seconds)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e

        local_str = when_dt.strftime("%Y-%m-%d %H:%M %Z")
        rel_seconds = max(
            0, _to_epoch_seconds(when_dt) - _to_epoch_seconds(_now_local())
        )
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
        _ensure_launchd_scheduler_installed()
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
    tasks = _load_schedule_tasks()
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
        _save_schedule_tasks(tasks)


@schedule_app.command("list")
def schedule_list():
    """List scheduled tasks."""
    tasks = _load_schedule_tasks()
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
def schedule_clear_completed():
    """Remove tasks with status 'completed'."""
    removed = _auto_clear_completed_tasks()
    if removed:
        console.print(f"✅ Removed [bold]{removed}[/bold] completed task(s).")
    else:
        console.print("No completed tasks to remove.")


def main():
    # Auto-clear completed tasks before any command runs to keep schedule tidy
    with contextlib.suppress(Exception):
        # Ignore cleanup errors; should never block commands
        removed = _auto_clear_completed_tasks()
        if removed:
            # Keep this silent in normal output; uncomment to log cleanup
            pass
    # Mount schedule sub-app
    app.add_typer(schedule_app, name="schedule")
    app()
