# Codesmith

Claude Code to Discord bridge - run Claude Code sessions through Discord.

## Features

- **Per-user sessions**: Each Discord user gets their own Claude Code session
- **Real-time output**: Claude Code output streams directly to Discord channels
- **Status embed**: Auto-updating pinned embed showing model, tokens, cost, and context usage
- **Slash commands**: Pass Claude Code commands through Discord (`/cc clear`, `/cc compact`, etc.)
- **SDK-based**: Uses official `@anthropic-ai/claude-code` npm package for reliable output

## Requirements

- Node.js 18+
- Discord bot token and application ID
- Anthropic API key OR users with Claude Max/Pro subscriptions

## Installation

### 1. Install dependencies
```bash
npm install
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your Discord bot token and app ID
```

### 3. Run the bot
```bash
# Development (with hot reload)
npm run dev

# Production
npm run build
npm start
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
Discord Bot (bot.ts)
    ↓
Session Manager (session.ts)
    ↓
Claude SDK Wrapper (claude.ts) ←→ Status Embed (embed.ts)
    ↓
@anthropic-ai/claude-code query()
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Discord bot token |
| `DISCORD_APP_ID` | Yes | Discord application ID |
| `ANTHROPIC_API_KEY` | No | Global API key (fallback) |
| `CODESMITH_CHANNEL_ID` | No | Restrict to specific channel |
| `CODESMITH_WORKSPACE_BASE` | No | Custom workspace directory |
| `CODESMITH_SESSION_TIMEOUT` | No | Session timeout in ms |
| `CODESMITH_DEFAULT_MODEL` | No | Default model name |

## License

MIT
