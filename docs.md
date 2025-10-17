# rp - Complete Documentation

This document provides comprehensive documentation for `rp`, a RunPod CLI wrapper tool that simplifies managing RunPod GPU instances through the command line. This documentation is structured to provide complete context about the tool's capabilities, configuration, and usage.

> **For LLMs**: This document contains complete technical specifications for the `rp` tool. It documents all CLI commands, configuration files, environment variables, and internal behavior. Use this as a reference to understand and assist with `rp` operations.

## Table of Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Command Reference](#command-reference)
4. [Configuration](#configuration)
5. [File Structure](#file-structure)
6. [Environment Variables](#environment-variables)
7. [Workflow Guide](#workflow-guide)
8. [Advanced Usage](#advanced-usage)
9. [Technical Details](#technical-details)

---

## Overview

`rp` is a command-line wrapper around the RunPod Python API that provides:

- **Simplified pod management**: Create, start, stop, and destroy GPU pods
- **Alias system**: Manage pods using memorable names instead of IDs
- **Template support**: Create reusable pod configurations with automatic numbering
- **Scheduling**: Schedule pod operations (e.g., stop after 2 hours)
- **SSH integration**: Automatic SSH config management
- **Setup automation**: Run custom scripts on pod creation/startup
- **Per-pod configuration**: Store settings like default working directories
- **Editor integration**: Direct integration with Cursor and SSH shells

### Key Features

- Persistent local configuration stored in `~/.config/rp/`
- Automatic SSH configuration management in `~/.ssh/config`
- Scheduled task execution via macOS launchd (background daemon)
- Template-based pod creation with auto-incrementing aliases
- Per-pod configuration for default paths and settings

---

## Installation

### Requirements

- Python 3.13 or higher
- `uv` package manager
- RunPod API key
- SSH access to RunPod instances

### Install with uv

```bash
uv tool install https://github.com/Arrrlex/rp.git
```

### Upgrade

```bash
uv tool upgrade rp
```

### Uninstall

```bash
uv tool uninstall rp
```

### First Run

On first run, `rp` will prompt for your RunPod API key and save it to `~/.config/rp/runpod_api_key`. To avoid saving the key in plaintext, set the `RUNPOD_API_KEY` environment variable before running commands.

---

## Command Reference

### Core Pod Management

#### `rp create`

Create a new pod and add it to local configuration.

**Syntax:**
```bash
# Create from template
rp create <template_id>
rp create <template_id> --alias <alias>

# Create with explicit parameters
rp create --alias <alias> --gpu <gpu_spec> --storage <size> [options]
```

**Arguments:**
- `<template_id>`: Template identifier to use (e.g., `ml-training`) - optional if using `--alias` mode

**Options:**
- `--alias <alias>`: SSH host alias (e.g., `my-pod-1`) - required when not using template, optional to override template alias
- `--gpu <spec>`: GPU specification (e.g., `2xA100`, `1xH100`, `4xRTX4090`) - can override template
- `--storage <size>`: Volume size (e.g., `500GB`, `1TB`) - can override template
- `--container-disk <size>`: Container disk size (default: `20GB`) - can override template
- `--image <image>`: Docker image (default: `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04`) - can override template
- `--config <key=value>`: Set pod configuration (e.g., `path=/workspace/project`). Can be specified multiple times
- `--force, -f`: Overwrite existing alias if it exists
- `--dry-run`: Show what would be created without creating

**Examples:**
```bash
# Create with explicit parameters
rp create --alias my-pod --gpu 2xA100 --storage 500GB

# Create with custom image
rp create --alias my-pod --gpu 1xH100 --storage 1TB --image nvidia/cuda:12.0.0-devel-ubuntu22.04

# Create from template (auto-numbered alias)
rp create ml-training

# Create from template with custom alias (overrides template naming)
rp create ml-training --alias custom-pod

# Create from template and override GPU
rp create ml-training --gpu 8xH100

# Create with config
rp create --alias my-pod --gpu 2xA100 --storage 500GB --config path=/workspace/myproject

# Dry run to preview
rp create --alias my-pod --gpu 2xA100 --storage 500GB --dry-run
```

**Behavior:**
1. Creates the pod via RunPod API
2. Adds alias to local configuration (`~/.config/rp/pods.json`)
3. Applies any config values specified via `--config` flags
4. Waits for pod to be running and SSH to be available
5. Updates SSH config (`~/.ssh/config`)
6. Runs setup scripts (`setup_local.sh` and `setup_remote.sh`)

---

#### `rp start`

Start a stopped pod.

**Syntax:**
```bash
rp start <alias>
```

**Arguments:**
- `<alias>`: Pod alias to start

**Example:**
```bash
rp start my-pod
```

**Behavior:**
1. Starts the pod via RunPod API
2. Waits for pod to be running
3. Updates SSH config with current IP/port
4. Runs setup scripts

---

#### `rp stop`

Stop a running pod, either immediately or scheduled for later.

**Syntax:**
```bash
rp stop <alias> [options]
```

**Arguments:**
- `<alias>`: Pod alias to stop

**Options:**
- `--at <time>`: Schedule stop at a specific time
- `--in <duration>`: Schedule stop after a duration
- `--dry-run`: Show what would happen without performing the action

**Time Formats (--at):**
- `"HH:MM"` - Today at specified time (or tomorrow if past)
- `"YYYY-MM-DD HH:MM"` - Specific date and time
- `"tomorrow HH:MM"` - Tomorrow at specified time
- Any format parseable by Python's `dateutil.parser`

**Duration Formats (--in):**
- `"3h"` - 3 hours
- `"45m"` - 45 minutes
- `"1d2h30m"` - 1 day, 2 hours, 30 minutes
- `"2h30m"` - 2 hours, 30 minutes

**Examples:**
```bash
# Stop immediately
rp stop my-pod

# Stop at 10 PM today (or tomorrow if past 10 PM)
rp stop my-pod --at "22:00"

# Stop tomorrow morning
rp stop my-pod --at "tomorrow 09:30"

# Stop in 2 hours
rp stop my-pod --in "2h"

# Stop in 1 day and 3 hours
rp stop my-pod --in "1d3h"
```

**Behavior (immediate):**
1. Stops pod via RunPod API
2. Removes SSH config entry

**Behavior (scheduled):**
1. Creates a scheduled task
2. Installs/updates macOS launchd agent (if on macOS)
3. Task will execute at specified time

---

#### `rp destroy`

Terminate a pod, remove it from configuration, and clean up SSH config.

**Syntax:**
```bash
rp destroy <alias> [options]
```

**Arguments:**
- `<alias>`: Pod alias to destroy

**Options:**
- `--force, -f`: Skip confirmation prompt

**Examples:**
```bash
# Destroy with confirmation prompt
rp destroy my-pod

# Destroy without confirmation
rp destroy my-pod --force
```

**Behavior:**
1. Prompts for confirmation (unless --force is used)
2. Terminates pod via RunPod API (stops first if running)
3. Removes alias from local configuration
4. Removes SSH config entry

**Warning:** This permanently deletes the pod and all data on it.

---

### Alias Management

#### `rp track`

Track an existing RunPod pod with an alias.

**Syntax:**
```bash
rp track <alias> <pod_id> [options]
```

**Arguments:**
- `<alias>`: Alias to assign
- `<pod_id>`: RunPod pod ID (e.g., `89qgenjznh5t2j`)

**Options:**
- `--force, -f`: Overwrite existing alias

**Example:**
```bash
# Track a pod created through RunPod website
rp track my-existing-pod 89qgenjznh5t2j
```

---

#### `rp untrack`

Stop tracking a pod (removes alias mapping, does not terminate the pod).

**Syntax:**
```bash
rp untrack <alias> [options]
```

**Arguments:**
- `<alias>`: Alias to remove

**Options:**
- `--missing-ok`: Don't error if alias doesn't exist

**Example:**
```bash
rp untrack my-pod
```

---

#### `rp list`

List all pods with their status and configuration.

**Syntax:**
```bash
rp list
```

**Output columns:**
- **Alias**: Pod alias
- **ID**: RunPod pod ID
- **Status**: `running`, `stopped`, or `invalid` (pod doesn't exist)
- **Config Path**: Default path if configured

**Example output:**
```
Alias           ID              Status    Config Path
────────────────────────────────────────────────────────
my-pod-1        89qgenjznh5t2j  running   /workspace/project
my-pod-2        k3nf83hdk3nd92  stopped   -
```

---

#### `rp show`

Show detailed information about a specific pod.

**Syntax:**
```bash
rp show <alias>
```

**Arguments:**
- `<alias>`: Pod alias to show details for

**Example:**
```bash
rp show my-pod
```

**Output includes:**
- Pod ID and status
- GPU type and count
- Storage (volume and container disk)
- Cost per hour
- IP address and SSH port (if running)
- Docker image
- Configuration values (if any)
- Scheduled tasks (if any)

**Example output:**
```
Pod Details: my-pod
============================================================
ID:        89qgenjznh5t2j
Status:    RUNNING
GPU:       2xH100PCIE
Storage:   500GB
Container:  20GB
Cost:      $3.200/hour
IP:        123.45.67.89:12345
Image:     runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn...

Configuration:
  path: /workspace/myproject

Scheduled Tasks:
  • stop at 2025-01-15 22:00 (id=550e8400)
============================================================
```

---

#### `rp clean`

Remove invalid aliases, prune orphaned SSH config entries, and clean completed tasks.

**Syntax:**
```bash
rp clean
```

**Behavior:**
1. Queries RunPod API for each alias
2. Removes aliases for pods that no longer exist
3. Removes SSH config entries for removed aliases
4. Removes completed and cancelled scheduled tasks

**Note:** This command runs automatically after every API command (create, start, stop, destroy) to keep things tidy.

---

### Connection Commands

#### `rp cursor`

Open Cursor editor connected to a pod via SSH.

**Syntax:**
```bash
rp cursor <alias> [path]
```

**Arguments:**
- `<alias>`: Pod alias to connect to
- `[path]`: Remote path to open (optional)

**Default path:**
- Uses configured default path if set (see `rp config`)
- Falls back to `/workspace` if no path configured

**Example:**
```bash
# Open at configured default path or /workspace
rp cursor my-pod

# Open at specific path
rp cursor my-pod /workspace/myproject
```

**Requirements:**
- Cursor must be installed and in PATH
- SSH config must be set up (happens automatically with `rp create/start`)

---

#### `rp shell`

Open an interactive SSH shell to a pod.

**Syntax:**
```bash
rp shell <alias>
```

**Arguments:**
- `<alias>`: Pod alias to connect to

**Example:**
```bash
rp shell my-pod
```

**Behavior:**
- If default path configured: `cd`s into that directory automatically
- Enables SSH agent forwarding (`-A` flag)

---

### Pod Configuration

#### `rp config`

Get or set configuration values for a pod.

**Syntax:**
```bash
# Get a value
rp config <alias> <key>

# Set a single value
rp config <alias> key=value

# Set multiple values
rp config <alias> key1=value1 key2=value2

# Clear a value
rp config <alias> key=
```

**Arguments:**
- `<alias>`: Pod alias
- `<key>`: Configuration key (for get mode)
- `key=value`: Configuration key-value pair (for set mode)

**Valid keys:**
- `path`: Default working directory path

**Examples:**
```bash
# Get default path
rp config my-pod path

# Set default path
rp config my-pod path=/workspace/myproject

# Set multiple values
rp config my-pod path=/workspace/x path2=/workspace/y

# Clear default path
rp config my-pod path=
```

**Behavior:**
- When setting values, shows whether each config is new or being overridden (with previous value)
- To list all configuration values, use `rp show <alias>` which displays config along with other pod details

---

### Template Management

Templates allow you to save common pod configurations and reuse them with automatic alias numbering.

#### `rp template create`

Create a new pod template.

**Syntax:**
```bash
rp template create <identifier> --alias-pattern <pattern> --gpu <spec> --storage <size> [options]
```

**Arguments:**
- `<identifier>`: Template identifier (e.g., `ml-training`)

**Options:**
- `--alias-pattern <pattern>`: Alias pattern with `{i}` placeholder (e.g., `ml-training-{i}`) (required)
- `--gpu <spec>`: GPU specification (required)
- `--storage <size>`: Storage size (required)
- `--container-disk <size>`: Container disk size (optional)
- `--image <image>`: Docker image (optional)
- `--config <key=value>`: Default pod configuration (e.g., `path=/workspace/project`). Can be specified multiple times
- `--force, -f`: Overwrite existing template

**Examples:**
```bash
# Create basic template
rp template create ml-training --alias-pattern "ml-training-{i}" --gpu 2xA100 --storage 1TB

# Create template with default path config
rp template create ml-training --alias-pattern "ml-training-{i}" --gpu 2xA100 --storage 1TB \
  --config path=/workspace/ml
```

**Note:** The `{i}` placeholder is replaced with the next available number when creating a pod from the template. Config values specified in the template are automatically applied to all pods created from that template.

---

#### `rp template list`

List all pod templates.

**Syntax:**
```bash
rp template list
```

**Output:**
```
Pod Templates
────────────────────────────────────────────────────────────────────
Identifier    Alias Template    GPU      Storage  Container Disk  Image
────────────────────────────────────────────────────────────────────
ml-training   ml-training-{i}   2xA100   1TB      (default: 20GB) (default)
```

---

#### `rp template delete`

Delete a pod template.

**Syntax:**
```bash
rp template delete <identifier> [options]
```

**Arguments:**
- `<identifier>`: Template identifier

**Options:**
- `--missing-ok`: Don't error if template doesn't exist

**Example:**
```bash
rp template delete ml-training
```

---

### Schedule Management

#### `rp schedule list`

List all scheduled tasks.

**Syntax:**
```bash
rp schedule list
```

**Output columns:**
- **ID**: Task UUID
- **Action**: Operation to perform (e.g., `stop`)
- **Alias**: Target pod alias
- **When**: Scheduled execution time
- **Status**: `pending`, `completed`, `failed`, or `cancelled`

---

#### `rp schedule cancel`

Cancel a scheduled task.

**Syntax:**
```bash
rp schedule cancel <task_id>
```

**Arguments:**
- `<task_id>`: Task UUID from `rp schedule list`

**Example:**
```bash
rp schedule cancel 550e8400-e29b-41d4-a716-446655440000
```

**Note:** Completed and cancelled tasks are automatically cleaned up by `rp clean`, which runs after every API command.

---

### Internal Commands

#### `rp scheduler-tick`

Execute due scheduled tasks. This is called automatically by the macOS launchd agent.

**Syntax:**
```bash
rp scheduler-tick
```

**Note:** You generally don't need to run this manually. It's executed every minute by the background scheduler.

---

## Configuration

### Configuration Directory

All configuration is stored in `~/.config/rp/`.

**Directory structure:**
```
~/.config/rp/
├── runpod_api_key        # RunPod API key (optional)
├── pods.json             # Pod aliases and configuration
├── schedule.json         # Scheduled tasks
├── setup_remote.sh       # Remote setup script (optional)
└── setup_local.sh        # Local setup script (optional)
```

---

### Configuration Files

#### `pods.json`

Stores pod aliases, metadata, templates, and per-pod configuration.

**Format:**
```json
{
  "aliases": {},
  "pod_metadata": {
    "my-pod-1": {
      "pod_id": "89qgenjznh5t2j",
      "config": {
        "path": "/workspace/myproject"
      }
    }
  },
  "scheduled_tasks": [],
  "pod_templates": {
    "ml-training": {
      "identifier": "ml-training",
      "alias_template": "ml-training-{i}",
      "gpu_spec": "2xA100",
      "storage_spec": "1TB",
      "container_disk_spec": null,
      "image": null
    }
  }
}
```

**Fields:**
- `aliases`: Legacy format (alias → pod_id mapping)
- `pod_metadata`: New format with per-pod configuration
  - `pod_id`: RunPod instance ID
  - `config`: Per-pod settings
    - `path`: Default working directory
- `scheduled_tasks`: Array of scheduled tasks (deprecated, moved to `schedule.json`)
- `pod_templates`: Dictionary of pod templates

---

#### `schedule.json`

Stores scheduled tasks.

**Format:**
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "action": "stop",
    "alias": "my-pod",
    "when_epoch": 1704124800,
    "status": "pending",
    "created_at": "2025-01-01T12:00:00Z",
    "last_error": null
  }
]
```

**Task fields:**
- `id`: Unique task UUID
- `action`: Action to perform (`stop` is currently the only supported action)
- `alias`: Target pod alias
- `when_epoch`: Unix timestamp for execution
- `status`: Task status
  - `pending`: Not yet executed
  - `completed`: Successfully executed
  - `failed`: Execution failed
  - `cancelled`: Manually cancelled
- `created_at`: ISO 8601 timestamp of task creation
- `last_error`: Error message if status is `failed`

---

#### `runpod_api_key`

Contains the RunPod API key in plaintext.

**Format:** Single line with the API key

**Example:**
```
YOUR_RUNPOD_API_KEY_HERE
```

**Security note:** To avoid storing the key in plaintext, set the `RUNPOD_API_KEY` environment variable instead.

---

#### `setup_remote.sh`

Optional script that runs on the remote pod during startup (after `rp create` or `rp start`).

**When it runs:**
- After pod is created and SSH is available
- After pod is started from stopped state

**Environment:**
- Runs as root on the pod
- Has network access
- Can install packages, configure services, etc.

**Example:** See `assets/example_setup_remote.sh` in the repository

**Common use cases:**
- Install system packages
- Configure SSH for GitHub/GitLab
- Set environment variables
- Install development tools
- Configure Git settings

---

#### `setup_local.sh`

Optional script that runs locally when connecting to a pod.

**When it runs:**
- After `setup_remote.sh` completes
- During `rp create` and `rp start`

**Environment variables:**
- `$POD_HOST`: The pod alias (can be used with SSH/SCP)

**Example:** See `assets/example_setup_local.sh` in the repository

**Common use cases:**
- Copy SSH keys to pod
- Copy configuration files
- Upload credentials
- Run local preparation tasks

---

### SSH Configuration

`rp` automatically manages SSH configuration in `~/.ssh/config`.

**Managed SSH block format:**
```
Host my-pod
    # rp:managed alias=my-pod pod_id=89qgenjznh5t2j updated=2025-01-01T12:00:00Z
    HostName 123.456.789.0
    User root
    Port 12345
    IdentitiesOnly yes
    IdentityFile ~/.ssh/runpod
    ForwardAgent yes
```

**Marker:** Lines starting with `# rp:managed` identify blocks managed by `rp`. These blocks are automatically updated when pods are started and removed when pods are stopped or destroyed.

**Note:** Don't manually edit rp-managed blocks, as they will be overwritten.

---

### macOS Scheduler (launchd)

On macOS, `rp` uses launchd to execute scheduled tasks.

**Launchd agent location:**
```
~/Library/LaunchAgents/com.rp.scheduler.plist
```

**Log file:**
```
~/Library/Logs/rp-scheduler.log
```

**Configuration:**
- Runs every 60 seconds
- Executes `rp scheduler-tick` to check for due tasks
- Starts automatically at login
- Environment variables passed to agent:
  - `PATH`: Standard system paths
  - `RUNPOD_API_KEY`: If saved in config file

**Manual management:**
```bash
# Check if agent is running
launchctl print gui/$(id -u)/com.rp.scheduler

# Stop agent
launchctl bootout gui/$(id -u)/com.rp.scheduler

# Start agent
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rp.scheduler.plist

# Restart agent
launchctl kickstart -k gui/$(id -u)/com.rp.scheduler
```

---

## File Structure

### Configuration Files

| Path | Purpose | Format |
|------|---------|--------|
| `~/.config/rp/runpod_api_key` | RunPod API key | Plain text |
| `~/.config/rp/pods.json` | Pod aliases and configuration | JSON |
| `~/.config/rp/schedule.json` | Scheduled tasks | JSON |
| `~/.config/rp/setup_remote.sh` | Remote setup script | Bash script |
| `~/.config/rp/setup_local.sh` | Local setup script | Bash script |

### macOS Scheduler Files

| Path | Purpose |
|------|---------|
| `~/Library/LaunchAgents/com.rp.scheduler.plist` | Launchd configuration |
| `~/Library/Logs/rp-scheduler.log` | Scheduler execution log |

### SSH Configuration

| Path | Purpose |
|------|---------|
| `~/.ssh/config` | SSH client configuration (managed blocks) |
| `~/.ssh/runpod` | Default SSH key for RunPod pods |

---

## Environment Variables

### `RUNPOD_API_KEY`

RunPod API key for authentication.

**Priority:**
1. Environment variable `RUNPOD_API_KEY` (highest priority)
2. File `~/.config/rp/runpod_api_key`
3. Prompt user if neither exists

**Example:**
```bash
export RUNPOD_API_KEY="your_api_key_here"
rp list
```

### `POD_HOST`

Available in `setup_local.sh` script. Contains the pod alias being set up.

**Example usage in setup_local.sh:**
```bash
#!/bin/bash
scp ~/.ssh/my_key $POD_HOST:/root/.ssh/
```

---

## Workflow Guide

### Initial Setup

1. **Install rp:**
   ```bash
   uv tool install https://github.com/Arrrlex/rp.git
   ```

2. **Set up API key (optional):**
   ```bash
   # Option 1: Environment variable
   export RUNPOD_API_KEY="your_key"

   # Option 2: Let rp prompt and save it
   rp list
   ```

3. **Create setup scripts (optional):**
   ```bash
   mkdir -p ~/.config/rp
   cp assets/example_setup_remote.sh ~/.config/rp/setup_remote.sh
   cp assets/example_setup_local.sh ~/.config/rp/setup_local.sh
   chmod +x ~/.config/rp/*.sh
   # Edit these files to customize
   ```

---

### Creating and Using Pods

**Direct creation:**
```bash
# Create a pod
rp create --alias my-pod --gpu 2xA100 --storage 500GB

# Connect with Cursor
rp cursor my-pod

# Connect with SSH
rp shell my-pod

# Stop when done
rp stop my-pod
```

**Template-based workflow:**
```bash
# Create a template with default config
rp template create training --alias-pattern "train-{i}" --gpu 2xA100 --storage 1TB \
  --config path=/workspace/training

# Create pods from template (auto-numbered, inherits config)
rp create training  # Creates train-1 with path=/workspace/training
rp create training  # Creates train-2 with path=/workspace/training

# Or create with custom alias
rp create training --alias my-special-pod  # Uses template config, custom name

# List all pods
rp list
```

---

### Scheduling Pod Stops

```bash
# Create and work on pod
rp create --alias my-pod --gpu 2xH100 --storage 1TB

# Schedule it to stop in 8 hours
rp stop my-pod --in "8h"

# Check scheduled tasks
rp schedule list

# Cancel if needed
rp schedule cancel <task-id>
```

---

### Managing Existing Pods

```bash
# Track a pod created on RunPod website
rp track existing-pod 89qgenjznh5t2j

# List all pods
rp list

# Connect to it
rp shell existing-pod

# Set default path
rp config existing-pod path=/workspace/myproject

# Now cursor opens at that path by default
rp cursor existing-pod
```

---

### Cleaning Up

```bash
# Remove pods that no longer exist
rp clean

# Remove completed scheduled tasks
rp schedule clean

# Destroy a pod permanently
rp destroy my-pod
```

---

## Advanced Usage

### Custom Docker Images

You can specify custom Docker images when creating pods:

```bash
rp create --alias my-pod --gpu 2xA100 --storage 500GB \
  --image nvidia/cuda:12.0.0-devel-ubuntu22.04
```

Or in templates:

```bash
rp template create custom --alias-pattern "custom-{i}" \
  --gpu 2xA100 \
  --storage 500GB \
  --image myregistry/myimage:latest
```

---

### Container Disk Size

RunPod pods have both a persistent volume and a container disk. By default, the container disk is 20GB.

To specify a larger container disk:

```bash
rp create --alias my-pod --gpu 2xA100 --storage 500GB --container-disk 50GB
```

---

### Per-Pod Configuration

Configure default paths for each pod to streamline your workflow. There are three ways to set config values:

**1. During pod creation:**
```bash
rp create --alias my-pod --gpu 2xA100 --storage 500GB --config path=/workspace/myproject
```

**2. In templates:**
```bash
# Create template with default config
rp template create ml --alias-pattern "ml-{i}" --gpu 2xA100 --storage 1TB --config path=/workspace/ml

# All pods from this template inherit the config
rp create ml  # Gets path=/workspace/ml automatically
```

**3. After creation with `rp config`:**
```bash
rp config my-pod path=/workspace/myproject
```

**Usage:**
```bash
# Now these commands use the configured default path
rp cursor my-pod      # Opens at /workspace/myproject
rp shell my-pod       # CDs to /workspace/myproject
```

**Overriding template config:**
```bash
# Override template default when creating
rp create ml --config path=/workspace/custom
```

---

### Setup Script Environment Variables

**In setup_remote.sh:**
```bash
#!/bin/bash
# Example: Set environment variables for HuggingFace
echo 'export HF_HOME=/workspace/huggingface' >> ~/.bashrc
echo 'export UV_CACHE_DIR=/workspace/uv' >> ~/.bashrc
```

**In setup_local.sh:**
```bash
#!/bin/bash
# $POD_HOST is set to the pod alias
scp ~/.ssh/github_key $POD_HOST:/root/.ssh/github_key
scp ~/.config/myapp/config.yaml $POD_HOST:/workspace/
```

---

### Force Operations

Some commands support `--force` to overwrite existing data:

```bash
# Overwrite existing alias
rp track my-pod new-pod-id --force

# Overwrite existing template
rp template create training "train-{i}" --gpu 4xA100 --storage 2TB --force

# Overwrite existing alias when creating
rp create --alias my-pod --gpu 2xA100 --storage 500GB --force
```

---

### Dry Run Mode

Preview operations without executing them:

```bash
# See what would be created
rp create --alias my-pod --gpu 2xA100 --storage 500GB --dry-run

# See what would be scheduled
rp stop my-pod --in "2h" --dry-run
```

---

## Technical Details

### Pod Status

Pods can have three statuses:

1. **running**: Pod is active and accessible via SSH
2. **stopped**: Pod is paused (data persists)
3. **invalid**: Pod doesn't exist in RunPod (may have been deleted)

### GPU Specifications

GPU specifications are parsed and resolved through a two-stage process.

#### Format

GPU specs follow the format: `[<count>x]<model>`

Where:
- `<count>` (optional): Number of GPUs (1, 2, 3, etc.). If omitted, defaults to 1
- `x`: Separator between count and model (case-insensitive)
- `<model>`: GPU model identifier (case-insensitive)

#### Parsing Examples

**With count:**
- `2xA100` → 2 GPUs, model "A100"
- `4xH100-SXM` → 4 GPUs, model "H100-SXM"
- `8xRTX4090` → 8 GPUs, model "RTX4090"

**Without count (defaults to 1):**
- `H100` → 1 GPU, model "H100"
- `h100-nvl` → 1 GPU, model "H100-NVL"
- `A100-PCIE` → 1 GPU, model "A100-PCIE"

**Edge cases:**
- Model names are normalized to uppercase internally
- `x` can appear in the model name (e.g., `rtx4090` is valid)
- If `x` appears but the prefix isn't numeric, it's treated as part of the model name

#### GPU Model Resolution

After parsing, the model identifier is resolved to a specific RunPod GPU type ID using the following algorithm (see `api_client.py:find_gpu_type_id`):

1. **Query RunPod API**: Fetch list of available GPU types
2. **Match by identifier**: Search for GPUs where the model identifier appears in either:
   - The GPU's internal ID (e.g., `NVIDIA H100 PCIe`)
   - The GPU's display name (e.g., `H100 PCIe 80GB`)
3. **Prefer highest VRAM**: If multiple variants match (e.g., different VRAM sizes), select the one with the highest memory
4. **Error if no match**: If no GPU matches the identifier, raise an error with suggestions

#### Common GPU Identifiers

**H100 variants:**
- `H100` or `H100-SXM` → NVIDIA H100 SXM (80GB, highest VRAM variant preferred)
- `H100-PCIE` → NVIDIA H100 PCIe (80GB)
- `H100-NVL` → NVIDIA H100 NVL (94GB)

**A100 variants:**
- `A100` → NVIDIA A100 (80GB, highest VRAM variant preferred)
- `A100-SXM` → NVIDIA A100 SXM (80GB SXM variant)
- `A100-PCIE` → NVIDIA A100 PCIe (40GB or 80GB, highest preferred)

**Other GPUs:**
- `L40S` → NVIDIA L40S (48GB)
- `RTX4090` → NVIDIA RTX 4090 (24GB)
- `A40` → NVIDIA A40 (48GB)
- `V100` → NVIDIA V100 (16GB or 32GB, highest preferred)

#### Full Examples

```bash
# Simple specifications
rp create --alias my-pod --gpu H100 --storage 500GB           # 1x H100 (highest VRAM)
rp create --alias my-pod --gpu 2xA100 --storage 1TB           # 2x A100 (highest VRAM)
rp create --alias my-pod --gpu RTX4090 --storage 500GB        # 1x RTX 4090

# Specific variants
rp create --alias my-pod --gpu 4xH100-PCIE --storage 2TB      # 4x H100 PCIe
rp create --alias my-pod --gpu 2xH100-SXM --storage 1TB       # 2x H100 SXM
rp create --alias my-pod --gpu H100-NVL --storage 500GB       # 1x H100 NVL (94GB)
rp create --alias my-pod --gpu 8xA100-SXM --storage 4TB       # 8x A100 SXM

# Case insensitive
rp create --alias my-pod --gpu 2xa100 --storage 1TB           # Same as 2xA100
rp create --alias my-pod --gpu h100-pcie --storage 500GB      # Same as H100-PCIE
```

#### Troubleshooting GPU Specs

If you receive an error like `Could not find GPU type matching 'XXX'`:

1. **Check available GPUs**: Use the RunPod website or API to see what's currently available
2. **Try generic identifiers**: Use `H100` instead of `H100-XXX` to let the tool pick the best variant
3. **Check spelling**: GPU identifiers are matched as substrings, so `H100` will match `H100-SXM`, `H100-PCIE`, etc.
4. **Verify availability**: Some GPU types may not be available in your region or tier

### Storage Specifications

Storage specs support human-readable formats:

- `500GB` → 500 gigabytes
- `1TB` → 1000 gigabytes
- `2.5TB` → 2500 gigabytes

Internally converted to integer gigabytes.

### Alias Naming

Aliases must:
- Be valid SSH host names
- Be unique within your configuration
- Not contain spaces or special characters (use hyphens/underscores)

**Good aliases:**
- `my-pod-1`
- `training_gpu`
- `dev-h100`

**Bad aliases:**
- `my pod` (contains space)
- `pod@123` (contains special char)

### Template Auto-Numbering

Templates with `{i}` placeholders automatically find the next available number:

```bash
# Template: "train-{i}"
rp create --template training  # Creates train-1
rp create --template training  # Creates train-2
rp destroy train-1              # Remove train-1
rp create --template training  # Creates train-1 (reuses lowest available)
```

The algorithm finds the lowest `i ≥ 1` where the formatted alias doesn't exist.

**Alias Override:**
You can also override the template's alias format by providing an explicit alias:

```bash
rp create training --alias custom-name  # Uses template's GPU/storage, custom alias
```

This creates a pod with the template's configuration (GPU, storage, image, etc.) but with your specified alias instead of the auto-numbered one.

### SSH Config Management

SSH blocks are identified by the marker comment:
```
# rp:managed alias=<alias> pod_id=<id> updated=<timestamp>
```

All lines between this marker and the next `Host` directive (or end of file) are managed by `rp`.

**Operations:**
- **Create/Start**: Adds or updates the SSH block
- **Stop**: Removes the SSH block
- **Destroy**: Removes the SSH block
- **Clean**: Removes orphaned blocks (where alias no longer exists)

### Scheduler Implementation

**macOS (launchd):**
- Agent runs every 60 seconds
- Executes `rp scheduler-tick`
- Checks for tasks where `when_epoch <= current_time`
- Executes due tasks and marks them completed/failed

**Other platforms:**
- Scheduling is stored but not automatically executed
- Manual execution required: `rp scheduler-tick`

### API Client

The tool uses the RunPod Python SDK internally:

```python
import runpod
runpod.api_key = "<your_api_key>"
```

**API operations:**
- `runpod.get_pod()`: Get pod details
- `runpod.create_pod()`: Create new pod
- `runpod.start_pod()`: Start stopped pod
- `runpod.stop_pod()`: Stop running pod
- `runpod.terminate_pod()`: Permanently delete pod

### Error Handling

Errors are handled gracefully with informative messages:

- **Validation errors**: Invalid input (GPU spec, storage spec, time format)
- **Not found errors**: Alias or pod doesn't exist
- **Already exists errors**: Alias or template already exists (use `--force`)
- **API errors**: RunPod API failures (authentication, quota, etc.)
- **Scheduling errors**: Invalid time formats, conflicting options

### Data Models

The tool uses Pydantic models for type safety:

- `Pod`: Pod instance with metadata
- `PodCreateRequest`: Pod creation parameters
- `PodTemplate`: Template configuration
- `PodConfig`: Per-pod settings
- `ScheduleTask`: Scheduled task
- `SSHConfig`: SSH configuration
- `AppConfig`: Application configuration

### Architecture

**Layers:**
1. **CLI Layer** (`main.py`, `commands.py`): Command-line interface using Typer
2. **Service Layer** (`pod_manager.py`, `scheduler.py`, `ssh_manager.py`): Business logic
3. **Data Layer** (`models.py`, `config.py`): Data structures and persistence
4. **API Layer** (`api_client.py`): RunPod API integration

---

## Troubleshooting

### API Key Issues

**Problem:** "API key not found"

**Solution:**
```bash
# Set environment variable
export RUNPOD_API_KEY="your_key"

# Or save to config
echo "your_key" > ~/.config/rp/runpod_api_key
```

---

### SSH Connection Issues

**Problem:** "Permission denied" or "Connection refused"

**Solutions:**
1. Check pod is running: `rp list`
2. Start pod if stopped: `rp start <alias>`
3. Check SSH config exists: `cat ~/.ssh/config | grep <alias>`
4. Update SSH config: `rp start <alias>` (refreshes IP/port)

---

### Scheduler Not Running

**Problem:** Scheduled tasks not executing (macOS)

**Solutions:**
```bash
# Check if agent is loaded
launchctl print gui/$(id -u)/com.rp.scheduler

# Restart agent
launchctl kickstart -k gui/$(id -u)/com.rp.scheduler

# Check logs
tail -f ~/Library/Logs/rp-scheduler.log
```

---

### Invalid Pod Status

**Problem:** Pod shows as "invalid" in `rp list`

**Causes:**
- Pod was deleted through RunPod website
- Pod was terminated by RunPod (e.g., out of credits)

**Solution:**
```bash
# Clean up invalid aliases
rp clean
```

---

### Template Not Found

**Problem:** "Template 'foo' not found"

**Solution:**
```bash
# List all templates
rp template list

# Create template if it doesn't exist
rp template create foo --alias-pattern "foo-{i}" --gpu 2xA100 --storage 1TB
```

---

## Examples Repository

### Example: Development Workflow

```bash
# One-time setup with config in template
rp template create dev --alias-pattern "dev-{i}" --gpu 1xRTX4090 --storage 500GB \
  --config path=/workspace/myproject

# Daily workflow
rp create dev                       # Create dev-1 with path already configured
rp cursor dev-1                     # Opens at /workspace/myproject automatically
# ... work on project ...
rp stop dev-1 --in "8h"    # Auto-stop in 8 hours

# Next day
rp start dev-1                      # Resume working
rp cursor dev-1                     # Continue where you left off
```

---

### Example: Training Jobs

```bash
# Template for training runs with default workspace
rp template create train --alias-pattern "train-{i}" --gpu 4xA100 --storage 2TB \
  --config path=/workspace/experiments

# Start multiple training runs
rp create train  # train-1 (opens at /workspace/experiments)
rp create train  # train-2 (opens at /workspace/experiments)

# Schedule automatic shutdown
rp stop train-1 --in "24h"
rp stop train-2 --in "24h"

# Monitor progress (automatically CDs to /workspace/experiments)
rp shell train-1
```

---

### Example: Custom Setup Scripts

**~/.config/rp/setup_remote.sh:**
```bash
#!/bin/bash
set -ex

# Install system packages
apt-get update
apt-get install -y vim git tmux htop nvtop

# Install Python tools
curl -LsSf https://astral.sh/uv/install.sh | sh

# Configure Git
git config --global user.name "Your Name"
git config --global user.email "your@email.com"

# Set up environment
echo 'export HF_HOME=/workspace/huggingface' >> ~/.bashrc
echo 'export UV_CACHE_DIR=/workspace/uv' >> ~/.bashrc

# Clone repository
cd /workspace
git clone git@github.com:yourname/yourrepo.git
```

**~/.config/rp/setup_local.sh:**
```bash
#!/bin/bash
set -e

# Copy SSH keys
scp ~/.ssh/github_key $POD_HOST:/root/.ssh/
scp ~/.ssh/config $POD_HOST:/root/.ssh/

# Copy environment files
scp ~/.env.production $POD_HOST:/workspace/.env
```

---

## Appendix: Complete Command List

| Command | Description |
|---------|-------------|
| `rp create` | Create a new pod |
| `rp start` | Start a stopped pod |
| `rp stop` | Stop a running pod |
| `rp destroy` | Terminate a pod permanently |
| `rp track` | Track existing pod with an alias |
| `rp untrack` | Stop tracking pod (remove alias) |
| `rp list` | List all pods with status |
| `rp clean` | Remove invalid aliases and SSH config |
| `rp cursor` | Open Cursor editor connected to pod |
| `rp shell` | Open SSH shell to pod |
| `rp config` | Get or set pod configuration values |
| `rp template create` | Create a pod template |
| `rp template list` | List all templates |
| `rp template delete` | Delete a template |
| `rp schedule list` | List scheduled tasks |
| `rp schedule cancel` | Cancel a scheduled task |
| `rp schedule clean` | Remove completed tasks |
| `rp scheduler-tick` | Execute due tasks (internal) |

---

## Version Information

This documentation is for `rp` version 0.1.0.

**Requirements:**
- Python 3.13+
- uv package manager
- macOS (for automatic scheduling)

**Project Repository:** https://github.com/Arrrlex/rp

---

## Contributing

For issues, feature requests, or contributions, please visit the GitHub repository.

---

*This documentation was generated to provide complete context for both humans and LLMs working with the `rp` tool.*
