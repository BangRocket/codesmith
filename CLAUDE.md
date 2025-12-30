# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
poetry install

# Run the bot
poetry run python -m codesmith.bot

# Lint
poetry run ruff check codesmith/

# Format
poetry run ruff format codesmith/

# VPS setup (run as root)
sudo ./scripts/setup_vps.sh
```

## Architecture

Codesmith bridges Claude Code CLI sessions to Discord. Each Discord user gets an isolated Claude Code session running in a bubblewrap sandbox.

**Data Flow:**
```
Discord message → bot.py → SessionManager → PTYSession → bwrap sandbox → Claude Code CLI
                                         ↓
Discord channel ← bot.py ← OutputBuffer ← PTYSession (async read)
```

**Key Components:**

- `bot.py`: Discord bot using py-cord. Handles slash commands (`/cc start|stop|clear|compact|model|status|login|logout`), routes user messages to sessions, handles OAuth credential paste via DMs, and manages output delivery to channels.

- `auth.py`: Authentication module supporting hybrid auth. Priority: per-user OAuth credentials → global API key → none. Validates and stores `.credentials.json` from users' local Claude Code installs.

- `session_manager.py`: Maps Discord user IDs to `UserSession` instances. Handles session lifecycle, output callbacks, and cleanup of expired/dead sessions. Uses a singleton pattern via `get_session_manager()`.

- `pty_handler.py`: Manages PTY pairs for Claude Code processes. Provides async read/write, terminal resizing, and signal handling. Each session gets its own PTY for proper terminal emulation.

- `sandbox_runner.py`: Builds bubblewrap commands for namespace isolation. Mounts minimal read-only root filesystem, user workspace as read-write at `/workspace`, passes through network for API access.

- `output_parser.py`: Uses `pyte` terminal emulator to track screen state. Extracts statusbar (model, tokens, cost, context%) from bottom line. Handles ANSI stripping and Discord message chunking (2000 char limit with code block awareness).

- `status_embed.py`: Creates and updates pinned Discord embeds showing session status. Parses statusbar text with regex to extract metrics. Color-codes context usage (green < 70% < yellow < 90% < red).

**Session Isolation:**
- Each user gets a workspace at `/var/codesmith/workspaces/<user_id>`
- bubblewrap provides: user namespace, PID namespace, UTS namespace, cgroup isolation
- Network is shared (required for Anthropic API calls)
- `--dangerously-skip-permissions` flag applies only inside the sandbox

## Authentication

Supports two authentication methods (checked in order):

1. **Per-user OAuth** (`/cc login`): Users authenticate with their own Claude Max/Pro subscription by pasting `~/.claude/.credentials.json` content via Discord DM. Credentials stored in user's workspace.

2. **Global API key** (`ANTHROPIC_API_KEY` env var): Shared API key for all users. Falls back if no per-user OAuth.

Auth flow in code: `auth.get_auth_method(user_id)` returns `AuthMethod.OAUTH`, `API_KEY`, or `NONE`. Session start fails early with helpful message if `NONE`.
