"""
SSH configuration management service for the RunPod CLI wrapper.

This module provides functionality for managing SSH configuration files,
including creating, updating, and cleaning up host entries for RunPod instances.
"""

import contextlib
import re
from datetime import UTC, datetime
from pathlib import Path

from rp.config import MARKER_PREFIX, SSH_CONFIG_FILE
from rp.core.models import SSHConfig
from rp.utils.errors import SSHError


class SSHManager:
    """Service for managing SSH configuration."""

    def __init__(self, ssh_config_path: Path | None = None):
        """Initialize the SSH manager with optional config path."""
        self.ssh_config_path = ssh_config_path or SSH_CONFIG_FILE

    def _load_ssh_config_lines(self) -> list[str]:
        """Load SSH config file as list of lines."""
        try:
            with self.ssh_config_path.open("r") as f:
                return f.readlines()
        except FileNotFoundError:
            return []

    def _write_ssh_config_lines(self, lines: list[str]) -> None:
        """Write SSH config file from list of lines."""
        self.ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.ssh_config_path.open("w") as f:
                f.writelines(lines)
        except Exception as e:
            raise SSHError.config_update_failed(str(e)) from e

    def _parse_ssh_blocks(self, lines: list[str]) -> list[dict]:
        """Parse SSH config into blocks, each starting with 'Host' line."""
        blocks = []
        i = 0

        while i < len(lines):
            line = lines[i]
            match = re.match(r"^\s*Host\s+(.+)$", line)

            if match:
                start = i
                # Find end of block (next Host line or EOF)
                i += 1
                while i < len(lines) and not re.match(r"^\s*Host\s+", lines[i]):
                    i += 1
                end = i

                # Extract host names and check if managed
                host_names = match.group(1).strip().split()
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

    def update_host_config(self, ssh_config: SSHConfig) -> None:
        """Create or update SSH host configuration."""
        lines = self._load_ssh_config_lines()
        blocks = self._parse_ssh_blocks(lines)

        # Generate updated timestamp
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Generate new block content
        new_block_lines = ssh_config.to_ssh_block(timestamp)

        # Find existing block for this alias
        target_block = None
        for block in blocks:
            if ssh_config.alias in block["hosts"]:
                target_block = block
                break

        if target_block is None:
            # Append new block at end
            if lines and lines[-1].strip() != "":
                lines.append("\n")  # Add separator if needed
            lines.extend(new_block_lines)
        else:
            # Replace existing block
            start, end = target_block["start"], target_block["end"]
            new_lines = lines[:start] + new_block_lines + lines[end:]
            lines = new_lines

        self._write_ssh_config_lines(lines)

    def remove_host_config(self, alias: str) -> bool:
        """Remove SSH host configuration for an alias."""
        lines = self._load_ssh_config_lines()
        if not lines:
            return False

        blocks = self._parse_ssh_blocks(lines)
        blocks_to_remove = []

        for block in blocks:
            if block["managed"] and alias in block["hosts"]:
                blocks_to_remove.append((block["start"], block["end"]))

        if not blocks_to_remove:
            return False

        # Remove blocks (process in reverse order to maintain indices)
        new_lines = []
        current_pos = 0

        for start, end in blocks_to_remove:
            new_lines.extend(lines[current_pos:start])
            current_pos = end

        new_lines.extend(lines[current_pos:])

        self._write_ssh_config_lines(new_lines)
        return True

    def prune_managed_blocks(self, valid_aliases: set[str]) -> int:
        """Remove managed blocks whose aliases are not in valid_aliases."""
        lines = self._load_ssh_config_lines()
        if not lines:
            return 0

        blocks = self._parse_ssh_blocks(lines)
        blocks_to_remove = []

        for block in blocks:
            if not block["managed"]:
                continue

            # Remove if none of the hosts in this block are valid
            if not any(host in valid_aliases for host in block["hosts"]):
                blocks_to_remove.append((block["start"], block["end"]))

        if not blocks_to_remove:
            return 0

        # Remove blocks
        new_lines = []
        current_pos = 0

        for start, end in blocks_to_remove:
            new_lines.extend(lines[current_pos:start])
            current_pos = end

        new_lines.extend(lines[current_pos:])

        self._write_ssh_config_lines(new_lines)
        return len(blocks_to_remove)

    def get_host_config(self, alias: str) -> SSHConfig | None:
        """Get SSH configuration for a host alias."""
        lines = self._load_ssh_config_lines()
        blocks = self._parse_ssh_blocks(lines)

        for block in blocks:
            if alias in block["hosts"] and block["managed"]:
                # Extract configuration from the block
                hostname = None
                port = None
                user = "root"
                identity_file = "~/.ssh/runpod"
                pod_id = None

                for i in range(block["start"] + 1, block["end"]):
                    line = lines[i].strip()

                    if line.startswith("HostName "):
                        hostname = line.split("HostName ", 1)[1].strip()
                    elif line.startswith("Port "):
                        with contextlib.suppress(ValueError):
                            port = int(line.split("Port ", 1)[1].strip())
                    elif line.startswith("User "):
                        user = line.split("User ", 1)[1].strip()
                    elif line.startswith("IdentityFile "):
                        identity_file = line.split("IdentityFile ", 1)[1].strip()
                    elif line.startswith(MARKER_PREFIX) and " pod_id=" in line:
                        # Extract pod_id from marker
                        match = re.search(r"pod_id=([^\s]+)", line)
                        if match:
                            pod_id = match.group(1)

                if hostname and port and pod_id:
                    return SSHConfig(
                        alias=alias,
                        pod_id=pod_id,
                        hostname=hostname,
                        port=port,
                        user=user,
                        identity_file=identity_file,
                    )

        return None

    def list_managed_hosts(self) -> list[str]:
        """List all managed host aliases."""
        lines = self._load_ssh_config_lines()
        blocks = self._parse_ssh_blocks(lines)

        managed_hosts = []
        for block in blocks:
            if block["managed"]:
                managed_hosts.extend(block["hosts"])

        return sorted(set(managed_hosts))
