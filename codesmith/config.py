"""Configuration module for Codesmith."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Discord Configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CODESMITH_CHANNEL_ID = os.getenv("CODESMITH_CHANNEL_ID", "")  # Optional restriction

# Anthropic API Key (optional - passed to Claude Code processes if set)
# If not set, users can authenticate with their own Max/Pro subscription
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None

# Sandbox Configuration
_workspace_default = "/var/codesmith/workspaces"
WORKSPACE_BASE = Path(os.getenv("CODESMITH_WORKSPACE_BASE", _workspace_default))
SESSION_TIMEOUT = int(os.getenv("CODESMITH_SESSION_TIMEOUT", "3600"))  # 1 hour default
MAX_OUTPUT_CHARS = int(os.getenv("CODESMITH_MAX_OUTPUT_CHARS", "50000"))

# Discord Limits
DISCORD_MSG_LIMIT = 2000  # Discord message character limit
EMBED_UPDATE_INTERVAL = 3.0  # Seconds between status embed updates

# PTY Configuration
PTY_COLS = 120  # Terminal width
PTY_ROWS = 40   # Terminal height

# bubblewrap paths
BWRAP_PATH = "/usr/bin/bwrap"
CLAUDE_CODE_PATH = "/usr/bin/claude"  # Global install location


def is_configured() -> bool:
    """Check if required configuration is present.

    Note: ANTHROPIC_API_KEY is optional - users can authenticate
    with their own Claude Max/Pro subscription via /cc login.
    """
    return bool(DISCORD_BOT_TOKEN)


def get_user_workspace(user_id: str) -> Path:
    """Get the workspace directory for a specific user."""
    # Sanitize user_id for filesystem safety
    safe_id = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return WORKSPACE_BASE / safe_id


def ensure_workspace(user_id: str) -> Path:
    """Ensure user workspace directory exists and return path."""
    workspace = get_user_workspace(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
