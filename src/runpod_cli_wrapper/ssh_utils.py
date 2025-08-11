import re

from .config import SSH_CONFIG_FILE

MARKER_PREFIX = "# rp:managed"


def _build_marker(alias: str, pod_id: str) -> str:
    from datetime import UTC, datetime

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
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*Host\s+(.+)$", line)
        if m:
            start = i
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
    new_lines: list[str] = []
    cur = 0
    for start, end in to_delete_ranges:
        new_lines.extend(lines[cur:start])
        cur = end
    new_lines.extend(lines[cur:])
    _write_ssh_config_lines(new_lines)
    return len(to_delete_ranges)


def prune_rp_managed_blocks(valid_aliases: set[str]) -> int:
    lines = _load_ssh_config_lines()
    if not lines:
        return 0
    blocks = _parse_ssh_blocks(lines)
    to_delete_ranges: list[tuple[int, int]] = []
    for blk in blocks:
        if not blk["managed"]:
            continue
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
    lines = _load_ssh_config_lines()
    blocks = _parse_ssh_blocks(lines)

    new_block: list[str] = []
    new_block.append(f"Host {host_alias}\n")
    new_block.append(_build_marker(host_alias, pod_id))
    new_block.append(f"    HostName {new_hostname}\n")
    new_block.append("    User root\n")
    new_block.append(f"    Port {new_port}\n")
    new_block.append("    IdentitiesOnly yes\n")
    new_block.append("    IdentityFile ~/.ssh/runpod\n")

    target_block = None
    for blk in blocks:
        if host_alias in blk["hosts"]:
            target_block = blk
            break

    if target_block is None:
        if lines and lines[-1].strip() != "":
            lines.append("\n")
        lines.extend(new_block)
        _write_ssh_config_lines(lines)
        return

    start, end = target_block["start"], target_block["end"]
    new_lines = []
    new_lines.extend(lines[:start])
    new_lines.extend(new_block)
    new_lines.extend(lines[end:])
    _write_ssh_config_lines(new_lines)
