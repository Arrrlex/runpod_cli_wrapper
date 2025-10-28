#!/bin/bash
set -ex

# Set DEBIAN_FRONTEND to noninteractive to prevent prompts
export DEBIAN_FRONTEND=noninteractive

chmod 600 /root/.ssh/github_key

# Set HuggingFace cache directory
echo 'export HF_HOME=/workspace/huggingface' >> ~/.bashrc
# Set uv cache directory
echo 'export UV_CACHE_DIR=/workspace/uv' >> ~/.bashrc

echo "--- Starting remote setup ---"

# 1. Update package list and install tools
echo "Updating apt and installing tools..."
apt-get update
# Added git to the install list
apt-get install -y vim curl git tmux nvtop

# 2. Install uv
echo "Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Configure SSH for GitHub
echo "Configuring SSH for GitHub..."
mkdir -p /root/.ssh
cat <<'EOF' > /root/.ssh/config
Host github.com
    HostName github.com
    User git
    IdentityFile /root/.ssh/github_key
    IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config

# 4. Configure Git
echo "Configuring Git..."
git config --global user.name "Alex McKenzie"
git config --global user.email "mail@alexmck.com"
git config --global core.editor "vim"

echo "--- Remote setup complete ---"
