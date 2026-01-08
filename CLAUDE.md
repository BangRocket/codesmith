# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
npm install

# Run the bot (development with hot reload)
npm run dev

# Build for production
npm run build

# Run production build
npm start

# Type check
npm run typecheck

# Lint
npm run lint
```

## Architecture

Codesmith bridges Claude Code to Discord using the official `@anthropic-ai/claude-code` SDK. Each Discord user gets their own Claude Code session.

**Data Flow:**
```
Discord message → bot.ts → SessionManager → Claude SDK query()
                                         ↓
Discord channel ← bot.ts ← async iterator ← Claude SDK
```

**Key Components:**

- `src/index.ts`: Main entry point. Handles graceful shutdown signals.

- `src/bot.ts`: Discord bot using discord.js. Handles slash commands (`/cc start|stop|clear|compact|model|status|login|logout`), routes user messages to sessions, handles OAuth credential paste via DMs.

- `src/claude.ts`: Wrapper around `@anthropic-ai/claude-code` SDK. Uses `query()` to run prompts and streams results via async iterator. Handles Discord message chunking (2000 char limit with code block awareness).

- `src/session.ts`: Maps Discord user IDs to `UserSession` instances. Handles session lifecycle, status callbacks, and cleanup of expired sessions. Uses singleton pattern via `getSessionManager()`.

- `src/auth.ts`: Authentication module supporting hybrid auth. Priority: per-user OAuth credentials → global API key → none. Validates and stores `.credentials.json` from users' local Claude Code installs.

- `src/embed.ts`: Creates and updates pinned Discord embeds showing session status. Color-codes context usage (green < 70% < yellow < 90% < red).

- `src/config.ts`: Configuration from environment variables.

- `src/types.ts`: TypeScript type definitions.

**Session Isolation:**
- Each user gets a workspace at `~/.codesmith/workspaces/<user_id>`
- Claude Code runs with `--dangerouslySkipPermissions` in the user's workspace
- Network access allowed (for Anthropic API calls)

## Authentication

Supports two authentication methods (checked in order):

1. **Per-user OAuth** (`/cc login`): Users authenticate with their own Claude Max/Pro subscription by pasting `~/.claude/.credentials.json` content via Discord DM. Credentials stored in user's workspace.

2. **Global API key** (`ANTHROPIC_API_KEY` env var): Shared API key for all users. Falls back if no per-user OAuth.

Auth flow in code: `getAuthMethod(userId)` returns `AuthMethod.OAUTH`, `API_KEY`, or `NONE`. Session start fails early with helpful message if `NONE`.
