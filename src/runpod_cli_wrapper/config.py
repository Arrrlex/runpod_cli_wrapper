import contextlib
import json
import os
from pathlib import Path

import runpod
import typer
from rich.console import Console

# --- CONFIGURATION (shared) ---
CONFIG_DIR = Path.home() / ".config" / "rp"
POD_CONFIG_FILE = CONFIG_DIR / "pods.json"
TEMPLATES_FILE = CONFIG_DIR / "templates.json"
API_KEY_FILE = CONFIG_DIR / "runpod_api_key"
REMOTE_SETUP_FILE = CONFIG_DIR / "setup_remote.sh"
LOCAL_SETUP_FILE = CONFIG_DIR / "setup_local.sh"

# The full path to your SSH config file.
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

# Scheduler storage and macOS launchd integration
SCHEDULE_FILE = CONFIG_DIR / "schedule.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABEL = "com.rp.scheduler"
LAUNCHD_PLIST = LAUNCH_AGENTS_DIR / f"{LAUNCHD_LABEL}.plist"
LOGS_DIR = Path.home() / "Library" / "Logs"
SCHEDULER_LOG_FILE = LOGS_DIR / "rp-scheduler.log"


console = Console()


def ensure_config_dir_exists() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def setup_runpod_api() -> None:
    """Ensure RunPod API key is available and set runpod.api_key."""
    if candidate := os.environ.get("RUNPOD_API_KEY"):
        api_key = candidate
    elif API_KEY_FILE.exists():
        api_key = API_KEY_FILE.read_text().strip()
    else:
        # Interactive prompt (hidden input)
        import getpass

        try:
            api_key = getpass.getpass("Enter RunPod API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(1)
        if not api_key:
            raise SystemExit(1)
        # Persist with restricted permissions
        ensure_config_dir_exists()
        with API_KEY_FILE.open("w") as f:
            f.write(api_key + "\n")
        with contextlib.suppress(Exception):
            os.chmod(API_KEY_FILE, 0o600)
        console.print("🔐 Saved RunPod API key for future use.")
    runpod.api_key = api_key


def load_pod_configs() -> dict:
    try:
        with POD_CONFIG_FILE.open("r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        console.print(
            f"[yellow]⚠️  Config file is not valid JSON: {POD_CONFIG_FILE}[/yellow]"
        )
        return {}


def save_pod_configs(pod_configs: dict) -> None:
    ensure_config_dir_exists()
    with POD_CONFIG_FILE.open("w") as f:
        json.dump(pod_configs, f, indent=2, sort_keys=True)
        f.write("\n")


def determine_pod_status(pod_id: str) -> str:
    """Return coarse status for a pod_id: 'running', 'stopped', or 'invalid'."""
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
    return "stopped"


def validate_host_alias(host_alias: str) -> str:
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
