#!/bin/bash
# Codesmith VPS Setup Script
# Run as root or with sudo

set -e

echo "=== Codesmith VPS Setup ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./setup_vps.sh)"
    exit 1
fi

# Enable unprivileged user namespaces (required for bubblewrap)
echo "Enabling unprivileged user namespaces..."
sysctl -w kernel.unprivileged_userns_clone=1
echo "kernel.unprivileged_userns_clone=1" >> /etc/sysctl.conf

# Update package list
echo "Updating package list..."
apt-get update

# Install bubblewrap
echo "Installing bubblewrap..."
apt-get install -y bubblewrap

# Install Node.js 20 (LTS)
echo "Installing Node.js 20..."
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
else
    echo "Node.js already installed: $(node --version)"
fi

# Install Claude Code globally
echo "Installing Claude Code..."
npm install -g @anthropic-ai/claude-code

# Verify Claude Code installation
if command -v claude &> /dev/null; then
    echo "Claude Code installed: $(claude --version)"
else
    echo "WARNING: Claude Code not found in PATH"
    echo "You may need to add npm global bin to PATH"
fi

# Create workspace directory
echo "Creating workspace directory..."
mkdir -p /var/codesmith/workspaces
chmod 755 /var/codesmith
chmod 755 /var/codesmith/workspaces

# Install Python dependencies (assuming Poetry is used)
echo "Installing Python 3.11+ if needed..."
if ! command -v python3.11 &> /dev/null; then
    apt-get install -y python3.11 python3.11-venv python3-pip
fi

# Install Poetry if not present
if ! command -v poetry &> /dev/null; then
    echo "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Set environment variables in .env file"
echo "2. Run 'poetry install' in the codesmith directory"
echo "3. Start the bot with 'poetry run python -m codesmith.bot'"
echo ""
echo "Required environment variables:"
echo "  DISCORD_BOT_TOKEN - Your Discord bot token"
echo "  ANTHROPIC_API_KEY - Your Anthropic API key"
