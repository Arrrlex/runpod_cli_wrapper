# RunPod CLI Wrapper

This is a little wrapper around runpod's python API. It provides some neat things like scheduling pod shutdowns, running scripts automatically on starting/creating a pod, and managing pod templates for quick deployment.

## 📚 Complete Documentation

**For comprehensive documentation of all commands, options, and configuration details, see [docs.md](docs.md).**

The `docs.md` file contains:
- Complete command reference with all options
- Detailed configuration file documentation
- Environment variables and settings
- Workflow guides and examples
- Technical details and troubleshooting

**💡 Tip for LLM users:** Give `docs.md` to your LLM for complete context on the `rp` tool. It's structured to provide everything needed to understand and work with the tool.

## Installation

Install using [uv](https://docs.astral.sh/uv/):

```bash
uv tool install https://github.com/Arrrlex/rp.git
```

## Upgrading

To upgrade to the latest version:

```bash
uv tool upgrade rp
```

## Uninstalling

To uninstall:

```bash
uv tool uninstall rp
```

## Usage

After installation, use the `rp` command to interact with your RunPod instances:

```bash
rp --help
```

This will show you all available commands and options for managing your pods.

The workflow is roughly:

1. **Create pods directly**: `rp create alex-ast-2 --gpu 2xH100 --storage 500GB` creates a pod and adds it to the list that `rp` manages
2. **Or use templates for repeated deployments**:
   - Create a template: `rp template create my-template "my-pod-{i}" --gpu 2xH100 --storage 500GB`
   - Use template: `rp create --template my-template` (automatically creates `my-pod-1`, `my-pod-2`, etc.)
3. **Track existing pods**: For any pods created using the RunPod website, `rp track <alias> <id>` adds it to `rp`'s local config
4. **List pods**: `rp list` shows you all rp's managed pods and their status (running, stopped, or invalid if they don't exist)
5. **View pod details**: `rp show <alias>` displays comprehensive information including GPU, storage, cost, uptime, and scheduled tasks
6. **Connect to pods**:
   - `rp shell <alias>` opens an interactive SSH shell to the pod
   - `rp cursor <alias> [path]` opens Cursor editor connected to the pod (defaults to /workspace or configured path)
7. **Configure pods**: Set per-pod configuration like default Cursor paths, see [Pod Configuration](#pod-configuration)
8. **Stop pods**: `rp stop <alias>` stops a pod. Alternatively you can schedule shutting down a pod, see [Scheduling](#scheduling)
9. **Destroy pods**: `rp destroy <alias>` terminates a pod (with confirmation prompt; use `--force` to skip)

The first time you run a `rp` command, it will ask you to provide your runpod API key. It will save this in `~/.config/rp/runpod_api_key`. If you don't want this saved in plaintext locally, make sure that the `RUNPOD_API_KEY` env var is set when you run `rp`.

### Pod Templates

Templates let you save common pod configurations and reuse them with automatic alias numbering:

```bash
# Create a template
rp template create ml-training "ml-training-{i}" --gpu 2xA100 --storage 1TB

# Use the template (creates ml-training-1, ml-training-2, etc.)
rp create --template ml-training

# Or override the alias when using a template
rp create custom-name --template ml-training

# List all templates
rp template list

# Delete a template
rp template delete ml-training
```

Templates automatically find the lowest available number for the `{i}` placeholder, so if `ml-training-1` exists, the next pod will be `ml-training-2`. You can also provide an explicit alias to override the template's naming scheme.

### Pod Configuration

You can configure per-pod settings like default working directory paths:

```bash
# Set a default path for a pod (used by both cursor and shell)
rp config set alex-ast-1 path /workspace/ast-goodfire

# Get a specific config value
rp config get alex-ast-1 path

# List all configuration for a pod
rp config list alex-ast-1

# Clear a config value
rp config set alex-ast-1 path
```

When you run `rp cursor alex-ast-1` or `rp shell alex-ast-1` without specifying a path, they will use the configured default path. The cursor command defaults to `/workspace` if no path is configured, and the shell command will cd into the configured path automatically.

### Scheduling

You can schedule pod shutdowns for later using the `--at` or `--in` options with the `stop` command:

```bash
# Schedule shutdown at a specific time
rp stop my-pod --at "22:00"
rp stop my-pod --at "2025-01-03 09:30"
rp stop my-pod --at "tomorrow 09:30"

# Schedule shutdown after a duration
rp stop my-pod --in "2h"
rp stop my-pod --in "1d2h30m"
```

Manage your scheduled tasks with the `schedule` subcommands:

```bash
rp schedule list              # View all scheduled tasks
rp schedule cancel <task-id>  # Cancel a specific task
rp schedule clean             # Remove completed and cancelled tasks
```

On macOS, the tool automatically sets up a background scheduler using launchd to execute tasks when they're due.

## Configuration

The CLI tool uses configuration files stored in `~/.config/rp/` to customize the setup process.

### Setup Scripts

Configure the tool by creating two setup scripts:

- **`~/.config/rp/setup_remote.sh`** - Script that runs on the remote pod during startup
- **`~/.config/rp/setup_local.sh`** - Script that runs locally when connecting to a pod. This will have access to the env var `$POD_HOST`.

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
