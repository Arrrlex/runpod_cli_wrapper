"""
Configuration utilities for the RunPod CLI wrapper.

This module provides configuration constants and utilities for the RunPod CLI
wrapper, including paths to configuration files and directories.
"""

from pathlib import Path

# --- CONFIGURATION ---
# Location to store alias→pod_id mappings
CONFIG_DIR = Path.home() / ".config" / "rp"
POD_CONFIG_FILE = CONFIG_DIR / "pods.json"
API_KEY_FILE = CONFIG_DIR / "runpod_api_key"
REMOTE_SETUP_FILE = CONFIG_DIR / "setup_remote.sh"
LOCAL_SETUP_FILE = CONFIG_DIR / "setup_local.sh"

# The full path to your SSH config file.
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

# Marker prefix for SSH config
MARKER_PREFIX = "# rp:managed"

# --- END CONFIGURATION ---

# Scheduler storage and macOS launchd integration
SCHEDULE_FILE = CONFIG_DIR / "schedule.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABEL = "com.rp.scheduler"
LAUNCHD_PLIST = LAUNCH_AGENTS_DIR / f"{LAUNCHD_LABEL}.plist"
LOGS_DIR = Path.home() / "Library" / "Logs"
SCHEDULER_LOG_FILE = LOGS_DIR / "rp-scheduler.log"


def ensure_config_dir_exists() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
