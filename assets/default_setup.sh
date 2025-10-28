#!/bin/bash
set -e

# Set DEBIAN_FRONTEND to noninteractive to prevent prompts
export DEBIAN_FRONTEND=noninteractive

echo "--- Starting pod setup ---"

echo "Updating apt and installing essential tools..."
apt-get update
apt-get install -y vim curl git tmux nvtop less htop jq unzip

echo "Installing NVM and Node.js..."
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
nvm install --lts

echo "Installing uv (Python package manager)..."
curl -LsSf https://astral.sh/uv/install.sh | sh

echo "Installing Starship prompt..."
curl -sS https://starship.rs/install.sh | sh -s -- -y

echo "Installing Claude Code CLI..."
npm install -g @anthropic-ai/claude-code

echo "Configuring Git..."
# Replace these with your own details
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
git config --global core.editor "vim"
git config --global core.pager 'less -FXR'
git config --global pull.ff "only"
git config --global push.autoSetupRemote true

echo "Creating .vimrc..."
cat <<'EOF' > ~/.vimrc
syntax on
set number
set tabstop=4
set shiftwidth=4
set expandtab
set ruler
EOF

echo "Creating .bashrc..."
cat > ~/.bashrc << 'EOF'
# If not running interactively, don't do anything. Must be first.
[ -z "$PS1" ] && return

# Load environment variables specific to RunPod
if [ -f /etc/rp_environment ]; then
    source /etc/rp_environment
fi

# All caches should be in /workspace (persists across pod restarts)
export XDG_CACHE_HOME="/workspace/.cache"
mkdir -p /workspace/.cache
chmod 755 /workspace/.cache

# All temporary files should be in /workspace
export TMPDIR="/workspace/tmp"
mkdir -p /workspace/tmp
chmod 777 /workspace/tmp

# Configure history
HISTSIZE=1000
HISTFILESIZE=2000
shopt -s histappend
export HISTCONTROL=ignoreboth

# Load NVM (Node Version Manager)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && source "$NVM_DIR/bash_completion"

# Use lesspipe for more friendly output with 'less'
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# Update window size after each command
shopt -s checkwinsize

# Enable color support for common commands
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# Display RunPod info
if [ -f /etc/runpod.txt ]; then
    cat /etc/runpod.txt
fi

# Add .local/bin to PATH
export PATH=~/.local/bin:$PATH

# Initialize Starship prompt. This MUST be the last thing to run.
eval "$(starship init bash)"
EOF

echo "--- Setup complete ---"
echo ""
echo "IMPORTANT: Edit ~/.config/rp/setup.sh to customize this script with your own:"
echo "  - Git name and email"
echo "  - Repository clones"
echo "  - Custom environment variables"
echo "  - Additional tools and packages"
