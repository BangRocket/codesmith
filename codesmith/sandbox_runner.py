"""bubblewrap sandbox runner for Claude Code sessions."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import (
    ANTHROPIC_API_KEY,
    BWRAP_PATH,
    CLAUDE_CODE_PATH,
    ensure_workspace,
)


def get_bwrap_command(user_id: str, workspace_path: Path) -> list[str]:
    """Build the bubblewrap command for sandboxed Claude Code execution.

    Args:
        user_id: User identifier for the session
        workspace_path: Path to user's workspace directory

    Returns:
        List of command arguments for subprocess
    """
    # Find actual paths for binaries
    node_path = shutil.which("node") or "/usr/bin/node"
    claude_path = shutil.which("claude") or CLAUDE_CODE_PATH

    # Get npm global prefix for node_modules
    npm_prefix = os.popen("npm config get prefix").read().strip()
    npm_lib = Path(npm_prefix) / "lib" / "node_modules"

    cmd = [
        BWRAP_PATH,
        # Create new namespaces
        "--unshare-user",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup",
        # Keep network access for API calls
        "--share-net",
        # Mount minimal root filesystem (read-only)
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc/ssl", "/etc/ssl",  # SSL certs
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",  # DNS
        "--ro-bind", "/etc/hosts", "/etc/hosts",
        # Mount node and npm directories
        "--ro-bind", str(Path(node_path).parent), str(Path(node_path).parent),
    ]

    # Add npm global modules if they exist
    if npm_lib.exists():
        cmd.extend(["--ro-bind", str(npm_lib), str(npm_lib)])

    # Mount user workspace as read-write
    cmd.extend([
        "--bind", str(workspace_path), "/workspace",
        # Create writable tmp and home
        "--tmpfs", "/tmp",
        "--tmpfs", "/home",
        # Set working directory
        "--chdir", "/workspace",
        # Set up /dev minimally
        "--dev", "/dev",
        # Proc filesystem
        "--proc", "/proc",
        # Environment variables
        "--setenv", "HOME", "/home/user",
        "--setenv", "USER", "user",
        "--setenv", "ANTHROPIC_API_KEY", ANTHROPIC_API_KEY,
        "--setenv", "TERM", "xterm-256color",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "NODE_PATH", str(npm_lib) if npm_lib.exists() else "",
        # Die with parent process
        "--die-with-parent",
        # The actual command to run
        "--",
        claude_path,
        "--dangerously-skip-permissions",
    ])

    return cmd


def setup_sandbox(user_id: str) -> tuple[Path, list[str]]:
    """Set up sandbox environment for a user.

    Args:
        user_id: User identifier

    Returns:
        Tuple of (workspace_path, bwrap_command)
    """
    workspace = ensure_workspace(user_id)

    # Create .claude directory for session data
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)

    # Build bwrap command
    cmd = get_bwrap_command(user_id, workspace)

    return workspace, cmd


def cleanup_sandbox(user_id: str, remove_workspace: bool = False) -> None:
    """Clean up sandbox resources.

    Args:
        user_id: User identifier
        remove_workspace: If True, remove the entire workspace directory
    """
    from .config import get_user_workspace

    workspace = get_user_workspace(user_id)

    if remove_workspace and workspace.exists():
        shutil.rmtree(workspace)
    elif workspace.exists():
        # Just clean up temp files, keep workspace
        tmp_dir = workspace / "tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)


def is_bwrap_available() -> bool:
    """Check if bubblewrap is available on the system."""
    return shutil.which("bwrap") is not None or Path(BWRAP_PATH).exists()


def is_claude_available() -> bool:
    """Check if Claude Code CLI is available."""
    return shutil.which("claude") is not None or Path(CLAUDE_CODE_PATH).exists()


def check_sandbox_requirements() -> dict[str, bool]:
    """Check all sandbox requirements.

    Returns:
        Dict with requirement names and availability status
    """
    return {
        "bubblewrap": is_bwrap_available(),
        "claude_code": is_claude_available(),
        "node": shutil.which("node") is not None,
        "anthropic_key": bool(ANTHROPIC_API_KEY),
    }
