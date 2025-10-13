"""
SSH configuration utilities for the RunPod CLI wrapper.

This module provides functionality for managing SSH configuration, including
loading, saving, and updating SSH config files. It includes support for parsing
and manipulating SSH config blocks.
"""

import json
import re
from datetime import UTC, datetime

import typer

from rp.config import (
    CONFIG_DIR,
    MARKER_PREFIX,
    POD_CONFIG_FILE,
    SSH_CONFIG_FILE,
)


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


def build_marker(alias: str, pod_id: str) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"    {MARKER_PREFIX} alias={alias} pod_id={pod_id} updated={ts}\n"


def load_ssh_config_lines() -> list[str]:
    try:
        with SSH_CONFIG_FILE.open("r") as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def write_ssh_config_lines(lines: list[str]) -> None:
    SSH_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SSH_CONFIG_FILE.open("w") as f:
        f.writelines(lines)


def parse_ssh_blocks(lines: list[str]) -> list[dict]:
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
    lines = load_ssh_config_lines()
    if not lines:
        return 0
    blocks = parse_ssh_blocks(lines)
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
    write_ssh_config_lines(new_lines)
    return len(to_delete_ranges)


def prune_rp_managed_blocks(valid_aliases: set[str]) -> int:
    """Remove rp-managed blocks whose alias is not in valid_aliases. Returns count removed."""
    lines = load_ssh_config_lines()
    if not lines:
        return 0
    blocks = parse_ssh_blocks(lines)
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
    write_ssh_config_lines(new_lines)
    return len(to_delete_ranges)


def update_ssh_config(
    host_alias: str, pod_id: str, new_hostname: str, new_port: int | str
) -> None:
    """Create or update a Host block for alias with rp marker, HostName and Port."""
    lines = load_ssh_config_lines()
    blocks = parse_ssh_blocks(lines)

    # Prepare updated block content
    new_block: list[str] = []
    new_block.append(f"Host {host_alias}\n")
    new_block.append(build_marker(host_alias, pod_id))
    new_block.append(f"    HostName {new_hostname}\n")
    new_block.append("    User root\n")
    new_block.append(f"    Port {new_port}\n")
    new_block.append("    IdentitiesOnly yes\n")
    new_block.append("    IdentityFile ~/.ssh/runpod\n")
    new_block.append("    ForwardAgent yes\n")

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
        write_ssh_config_lines(lines)
        return

    # Replace the existing block entirely to ensure marker and fields are correct
    start, end = target_block["start"], target_block["end"]
    new_lines = []
    new_lines.extend(lines[:start])
    new_lines.extend(new_block)
    new_lines.extend(lines[end:])
    write_ssh_config_lines(new_lines)
