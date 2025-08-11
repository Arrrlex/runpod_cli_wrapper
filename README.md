# RunPod CLI Wrapper

This is a little wrapper around runpod's python API. It provides some neat things like scheduling pod shutdowns and running scripts automatically on starting a pod.

## Installation

Install using `uv`:

```bash
uv tool install https://github.com/Arrrlex/runpod_cli_wrapper.git
```

## Upgrading

To upgrade to the latest version:

```bash
uv tool upgrade runpod-cli-wrapper
```

## Usage

After installation, use the `rp` command to interact with your RunPod instances:

```bash
rp --help
```

This will show you all available commands and options for managing your pods.

The workflow is roughly:

1. Spin up a new pod using the runpod website
2. `rp add <alias> <id>` to add the pod to your local setup
3. `rp start <alias>` starts up the pod and runs your setup scripts (more on that below)
4. `rp stop <alias>` stops the pod. Alternatively you can schedule shutting down a pod, see [Scheduling](#scheduling).

The first time you run a `rp` command, it will ask you to provide your runpod API key. It will save this in `~/config/rp/runpod_api_key`. If you don't want this saved in plaintext locally, make sure that the `RUNPOD_API_KEY` env var is set when you run `rp`.

### Scheduling

You can schedule pod shutdowns for later using the `--schedule-at` or `--schedule-in` options with the `stop` command:

```bash
# Schedule shutdown at a specific time
rp stop my-pod --schedule-at "22:00"
rp stop my-pod --schedule-at "2025-01-03 09:30"
rp stop my-pod --schedule-at "tomorrow 09:30"

# Schedule shutdown after a duration
rp stop my-pod --schedule-in "2h"
rp stop my-pod --schedule-in "1d2h30m"
```

Manage your scheduled tasks with the `schedule` subcommands:

```bash
rp schedule list              # View all scheduled tasks
rp schedule cancel <task-id>  # Cancel a specific task
```

On macOS, the tool automatically sets up a background scheduler using launchd to execute tasks when they're due.

## Configuration

The CLI tool uses configuration files stored in `~/.config/rp/` to customize the setup process.

### Setup Scripts

Configure the tool by creating two setup scripts:

- **`~/.config/rp/setup_remote.sh`** - Script that runs on the remote pod during startup
- **`~/.config/rp/setup_local.sh`** - Script that runs locally when connecting to a pod. This will auto-expand `{host}` to the hostname of the pod.

### Example Configuration Files

Example configuration files can be found in the `assets/` folder of this repository:

- [`assets/example_setup_remote.sh`](assets/example_setup_remote.sh) - Example remote setup script that:
  - Updates the system and installs essential tools (vim, curl, git, tmux, nvtop)
  - Installs uv (Python package manager)
  - Configures SSH for GitHub access
  - Sets up Git configuration
  - Sets environment variables for HuggingFace and uv cache directories

- [`assets/example_setup_local.sh`](assets/example_setup_local.sh) - Example local setup script that:
  - Copies SSH keys to the remote pod for GitHub access

Copy these example files to your configuration directory and customize them for your needs:

```bash
mkdir -p ~/.config/rp
cp assets/example_setup_remote.sh ~/.config/rp/setup_remote.sh
cp assets/example_setup_local.sh ~/.config/rp/setup_local.sh
```
