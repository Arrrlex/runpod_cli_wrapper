# RunPod CLI Wrapper

A batteries-included CLI for managing RunPod GPU pods. Get a fully configured development environment with one command, or customize everything to your needs.

**Key features:**
- 🚀 Default templates for common GPUs (h100, 2h100, 5090, a40)
- 🛠️ Automatic setup with essential dev tools, shell config, and SSH access
- 📦 Template system for repeatable deployments
- ⏰ Scheduled pod shutdowns to save costs
- 🔑 SSH config management
- ⚙️ Per-pod configuration

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

### Enable Tab Completion (Optional)

After installation, enable shell completion for alias and template tab-completion:

```bash
rp --install-completion
```

The command will auto-detect your shell. You may need to restart your shell or source your shell config file after installation. You can also manually specify a shell: `rp --install-completion bash` (or `zsh`, `fish`).

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

## Quick Start

Get a fully configured GPU pod with one command:

```bash
# Create a pod using a default template (h100, 2h100, 5090, a40)
rp create h100

# This automatically:
# - Creates the pod with sensible defaults (1xH100, 500GB storage)
# - Sets up SSH access
# - Installs essential tools (git, vim, tmux, nvtop, etc.)
# - Configures your shell with Starship prompt
# - Installs uv (Python), NVM/Node.js, and Claude Code CLI
# All tools and configs persist in /workspace across restarts

# Open Cursor editor connected to the pod
rp cursor

# Or open an SSH shell
rp shell

# Stop the pod when done (data in /workspace is preserved)
rp stop

# Start it again later - everything is still configured
rp start

# Destroy the pod when you're finished with it
rp destroy
```

**First-time Setup:**

The first time you run `rp create`, it will prompt you for:
- Your RunPod API key (saved to `~/.config/rp/runpod_api_key`)
- Your name and email for git commits

**Want More Control?**

- Customize the setup script: Edit `~/.config/rp/setup.sh`
- Create custom templates: `rp template create my-template --gpu 2xA100 --storage 1TB`
- Manual pod configuration: `rp create --alias my-pod --gpu 2xA100 --storage 1TB`

See the [complete documentation](docs.md) for advanced features like scheduling, templates, and per-pod configuration.

**Note:** If you're managing multiple pods, you'll need to specify which pod to use by providing its alias (e.g., `rp start my-pod`). When you have only one pod, `rp` automatically selects it. With multiple pods, `rp` will present an interactive menu to choose from. See [Working with Multiple Pods](#working-with-multiple-pods) for details.

## Working with Multiple Pods

When managing multiple pods, you have two options:

**1. Specify the alias explicitly:**
```bash
rp start my-pod-1
rp shell my-pod-2
rp stop my-pod-1
```

**2. Use interactive selection:**

If you don't provide an alias and have multiple pods, `rp` will show an interactive menu:

```bash
$ rp start
? Select a pod: (Use arrow keys)
 ❯ my-pod-1
   my-pod-2
   my-pod-3
```

**Additional commands for managing multiple pods:**

```bash
# List all your pods and their status
rp list

# Show detailed information about a specific pod
rp show my-pod-1

# Track an existing pod created via RunPod website
rp track my-existing-pod <pod-id>

# Stop tracking a pod (doesn't destroy it, just removes the alias)
rp untrack my-pod-1
```

### Pod Templates

Templates let you save common pod configurations and reuse them with automatic alias numbering.

**Default Templates**

`rp` ships with several default templates for common GPU configurations:

- **h100** - Single H100 GPU, 500GB storage
- **2h100** - Two H100 GPUs, 500GB storage
- **5090** - Single RTX 5090 GPU, 500GB storage
- **a40** - Single A40 GPU, 500GB storage

You can use these immediately:

```bash
# Create a pod using a default template
rp create h100        # Creates h100-1, h100-2, etc.
rp create 2h100       # Creates 2h100-1, 2h100-2, etc.
```

Default templates are read-only and cannot be deleted, but you can create your own templates with the same name to override them.

**Custom Templates**

Create your own templates for repeated deployments:

```bash
# Create a template with default config
rp template create ml-training --alias-pattern "ml-training-{i}" --gpu 2xA100 --storage 1TB \
  --config path=/workspace/ml

# Use the template (creates ml-training-1, ml-training-2, etc. with config)
rp create ml-training

# Or override the alias when using a template
rp create ml-training --alias custom-name

# Override template config when creating
rp create ml-training --config path=/workspace/custom

# List all templates
rp template list

# Delete a template
rp template delete ml-training
```

Templates automatically find the lowest available number for the `{i}` placeholder, so if `ml-training-1` exists, the next pod will be `ml-training-2`. You can also provide an explicit alias to override the template's naming scheme. Config values in templates are automatically applied to all pods created from that template.

### Pod Configuration

You can configure per-pod settings like default working directory paths in three ways:

**1. During pod creation:**
```bash
rp create --alias my-pod --gpu 2xA100 --storage 500GB --config path=/workspace/myproject
```

**2. In templates (applied to all pods from that template):**
```bash
rp template create ml --alias-pattern "ml-{i}" --gpu 2xA100 --storage 1TB --config path=/workspace/ml
```

**3. After creation using `rp config`:**
```bash
# Set a default path for a pod (used by both cursor and shell)
rp config alex-ast-1 path=/workspace/ast-goodfire

# Get a specific config value
rp config alex-ast-1 path

# Set multiple values at once
rp config alex-ast-1 path=/workspace/x path2=/workspace/y

# Clear a config value
rp config alex-ast-1 path=
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

### Setup Script

`rp` automatically runs a setup script on newly created pods to configure your development environment. A default setup script is provided that installs common tools and configures your shell.

**Default Setup Includes:**
- Essential tools: vim, curl, git, tmux, nvtop, less, htop, jq, unzip
- Python: uv package manager
- Node.js: NVM and latest LTS version
- Shell: Starship prompt with enhanced bash configuration
- Claude Code CLI
- Optimized cache directories in `/workspace` (persist across pod restarts)

**Customizing Your Setup:**

1. The first time you run `rp create`, a default setup script is copied to `~/.config/rp/setup.sh`
2. Edit this file to customize with your own:
   - Git name and email
   - Repository clones (use `GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"` for SSH)
   - Custom environment variables
   - Additional tools and packages

**Example customizations:**

```bash
# Add to ~/.config/rp/setup.sh

# Clone your repositories
GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" \
    git clone git@github.com:username/repo.git /workspace/repo

# Set custom environment variables
echo 'export OPENAI_API_KEY=your-key-here' >> ~/.bashrc
```

See [`assets/default_setup.sh`](assets/default_setup.sh) for the default setup script that will be customized with your git config.
