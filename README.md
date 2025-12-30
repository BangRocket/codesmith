# Codesmith

Claude Code to Discord bridge - run Claude Code sessions through Discord.

## Features

- **Per-user sessions**: Each Discord user gets their own isolated Claude Code session
- **Real-time output**: Claude Code output streams directly to Discord channels
- **Status embed**: Auto-updating pinned embed showing model, tokens, cost, and context usage
- **Slash commands**: Pass Claude Code commands through Discord (`/cc clear`, `/cc compact`, etc.)
- **Sandboxed execution**: Sessions run in bubblewrap namespace isolation

## Requirements

### VPS Requirements
- Linux with kernel 3.8+ (user namespaces support)
- `bubblewrap` package installed
- Node.js 20+
- Claude Code CLI (`@anthropic-ai/claude-code`)
- Python 3.11+

### Environment Variables
```bash
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key  # Optional if users authenticate via /cc login
CODESMITH_CHANNEL_ID=optional_channel_restriction
```

## Installation

### 1. Setup VPS
```bash
# Run the setup script as root
sudo ./scripts/setup_vps.sh
```

### 2. Install Python dependencies
```bash
cd codesmith
poetry install
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your tokens
```

### 4. Run the bot
```bash
poetry run python -m codesmith.bot
```

## Usage

### Discord Slash Commands

| Command | Description |
|---------|-------------|
| `/cc start` | Start a new Claude Code session |
| `/cc stop` | Stop your current session |
| `/cc login` | Authenticate with your Claude Max/Pro subscription |
| `/cc logout` | Remove your stored Claude credentials |
| `/cc clear` | Clear conversation history |
| `/cc compact` | Compact conversation to save context |
| `/cc model <name>` | Change the model |
| `/cc status` | Show session status |
| `/cc requirements` | Check sandbox requirements |

### Authentication

Two authentication methods are supported:

1. **Per-user OAuth** (recommended): Users run `/cc login` and follow the DM instructions to authenticate with their own Claude Max/Pro subscription.

2. **Shared API key**: Set `ANTHROPIC_API_KEY` environment variable for all users to share.

Per-user OAuth takes priority if both are configured.

### Interacting with Claude Code

Once you start a session with `/cc start`:
1. Any message you send in the channel goes directly to Claude Code
2. Claude Code's output appears in the channel
3. A pinned status embed shows current stats
4. Use `/cc stop` to end the session

## Architecture

```
Discord Bot (bot.py)
    ↓
Session Manager (session_manager.py)
    ↓
PTY Handler (pty_handler.py) ←→ Output Parser (output_parser.py)
    ↓
Sandbox Runner (sandbox_runner.py)
    ↓
bubblewrap (bwrap) → Claude Code CLI
```

## Security

Sessions run inside bubblewrap namespace isolation:
- Isolated filesystem (only workspace is writable)
- Isolated process namespace
- Network access allowed (for Anthropic API)
- `--dangerously-skip-permissions` only applies inside sandbox

## License

MIT
