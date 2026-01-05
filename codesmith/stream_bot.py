"""Codesmith Discord bot using stream-json mode.

This version uses Claude Code's --output-format stream-json for cleaner
output parsing instead of PTY terminal emulation.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

import discord
from discord.ext import commands, tasks

from .auth import (
    AuthMethod,
    delete_credentials,
    get_auth_method,
    has_valid_credentials,
    store_credentials,
    validate_credentials_json,
)
from .code_server import CodeServerManager, get_code_server_manager
from .config import (
    CODESMITH_CHANNEL_ID,
    DISCORD_BOT_TOKEN,
    EMBED_UPDATE_INTERVAL,
    is_configured,
)
from .output_parser import chunk_for_discord
from .status_embed import EmbedManager
from .stream_session import (
    StreamSession,
    StreamSessionManager,
    get_stream_session_manager,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("codesmith.stream")

# Intents for the bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


def check_requirements() -> dict[str, bool]:
    """Check requirements for stream mode."""
    return {
        "claude_code": shutil.which("claude") is not None,
        "node": shutil.which("node") is not None,
    }


class StreamCodesmithBot(commands.Bot):
    """Discord bot using stream-json for Claude Code sessions."""

    def __init__(self):
        """Initialize the bot."""
        super().__init__(
            command_prefix="!",
            intents=intents,
        )
        self.session_manager: StreamSessionManager = get_stream_session_manager()
        self.code_server_manager: CodeServerManager = get_code_server_manager()
        self.embed_manager = EmbedManager()
        self._user_channels: dict[str, discord.TextChannel] = {}
        self._pending_logins: set[str] = set()

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        self.add_application_command(cc_group)
        self.update_embeds.start()
        logger.info("Stream Codesmith bot initialized")

    async def on_ready(self) -> None:
        """Called when the bot is connected and ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        logger.info("Using stream-json output mode")

        reqs = check_requirements()
        for req, available in reqs.items():
            status = "OK" if available else "MISSING"
            logger.info(f"  {req}: {status}")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        if message.author.bot:
            return

        user_id = str(message.author.id)

        # Handle DMs for credential paste
        if isinstance(message.channel, discord.DMChannel):
            if user_id in self._pending_logins:
                await self._handle_credential_paste(message)
            return

        # Check if we should process this channel
        if CODESMITH_CHANNEL_ID and str(message.channel.id) != CODESMITH_CHANNEL_ID:
            return

        # Check if user has an active session
        session = self.session_manager.get_session(user_id)
        if not session:
            return

        # Check if session is busy
        if session.is_busy:
            try:
                await message.add_reaction("⏳")
            except discord.Forbidden:
                pass
            return

        # Process the message
        await self._process_message(session, message)

    async def _process_message(
        self,
        session: StreamSession,
        message: discord.Message,
    ) -> None:
        """Process a user message through Claude Code.

        Args:
            session: User's stream session
            message: Discord message
        """
        user_id = session.user_id
        channel = message.channel

        logger.info(f"[{user_id}] Processing message: {message.content[:50]}...")

        # Mark session as busy
        session.is_busy = True

        try:
            await message.add_reaction("📨")
        except discord.Forbidden:
            pass

        try:
            # Accumulate text for batched sending
            text_buffer = ""
            last_send = asyncio.get_event_loop().time()
            message_count = 0

            logger.info(f"[{user_id}] Starting Claude runner")

            async for msg in session.runner.run(message.content):
                message_count += 1
                session.touch()

                logger.debug(f"[{user_id}] Message {message_count}: type={msg.type}")

                # Handle assistant messages (Claude's response)
                if msg.text:
                    logger.info(f"[{user_id}] Got text ({len(msg.text)} chars)")
                    text_buffer += msg.text

                    # Send if buffer is large enough or enough time passed
                    current_time = asyncio.get_event_loop().time()
                    if len(text_buffer) > 1500 or (current_time - last_send) > 1.0:
                        buf_len = len(text_buffer)
                        logger.debug(f"[{user_id}] Sending buffer ({buf_len} chars)")
                        await self._send_text(channel, text_buffer)
                        text_buffer = ""
                        last_send = current_time

                # Handle tool use notifications
                if msg.tool_uses:
                    tool_names = [t.get("name", "unknown") for t in msg.tool_uses]
                    logger.info(f"[{user_id}] Tool uses: {tool_names}")
                    tools_str = ", ".join(f"`{n}`" for n in tool_names)
                    await channel.send(f"*Using tools: {tools_str}*")

                # Handle result (end of response)
                if msg.is_result:
                    cost = msg.cost_usd
                    logger.info(f"[{user_id}] Result: err={msg.is_error}, ${cost:.4f}")
                    session.update_stats(msg)

                    # Update embed with new stats
                    self.embed_manager.update_status(
                        user_id, session.format_statusbar()
                    )

            logger.info(f"[{user_id}] Finished processing ({message_count} messages)")

            # Send any remaining buffered text
            if text_buffer:
                logger.debug(f"[{user_id}] Sending final ({len(text_buffer)} chars)")
                await self._send_text(channel, text_buffer)

        except Exception as e:
            logger.exception(f"[{user_id}] Error processing message")
            await channel.send(f"**Error:** {e}")

        finally:
            session.is_busy = False
            logger.info(f"[{user_id}] Message processing complete")

    async def _send_text(
        self,
        channel: discord.TextChannel,
        text: str,
    ) -> None:
        """Send text to Discord, chunking if necessary.

        Args:
            channel: Discord channel
            text: Text to send
        """
        if not text.strip():
            return

        chunks = chunk_for_discord(text)
        for chunk in chunks:
            try:
                await channel.send(chunk)
            except discord.HTTPException as e:
                logger.error(f"Failed to send message: {e}")
                break

            if len(chunks) > 1:
                await asyncio.sleep(0.3)

    async def _handle_credential_paste(self, message: discord.Message) -> None:
        """Handle credential JSON pasted in DM."""
        user_id = str(message.author.id)
        content = message.content.strip()

        credentials = validate_credentials_json(content)

        if credentials is None:
            await message.channel.send(
                "Invalid credentials format. Please paste the entire contents of "
                "`~/.claude/.credentials.json` (should be valid JSON with "
                "`claudeAiOauth` key)."
            )
            return

        try:
            store_credentials(user_id, credentials)
            self._pending_logins.discard(user_id)

            await message.channel.send(
                "Credentials saved successfully! You can now use `/cc start` "
                "to begin a Claude Code session with your Max/Pro subscription."
            )
            logger.info(f"Stored OAuth credentials for user {user_id}")

        except Exception as e:
            logger.exception(f"Failed to store credentials for {user_id}")
            await message.channel.send(f"Failed to save credentials: {e}")

    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def update_embeds(self) -> None:
        """Periodically update all status embeds."""
        for user_id in list(self.embed_manager._embeds.keys()):
            embed = self.embed_manager.get(user_id)
            if embed and embed.message:
                try:
                    await embed.update_message()
                except Exception as e:
                    logger.error(f"Failed to update embed for {user_id}: {e}")

    async def start_user_session(
        self,
        ctx: discord.ApplicationContext,
    ) -> None:
        """Start a Claude Code session for a user."""
        user_id = str(ctx.author.id)

        # Check requirements
        reqs = check_requirements()
        if not reqs.get("claude_code"):
            await ctx.respond(
                "Claude Code CLI not found. Please install it first.",
                ephemeral=True,
            )
            return

        # Check authentication
        auth_method = get_auth_method(user_id)
        if auth_method == AuthMethod.NONE:
            await ctx.respond(
                "No authentication configured.\n"
                "Use `/cc login` to authenticate with your Claude Max/Pro "
                "subscription, or ask the server admin to set `ANTHROPIC_API_KEY`.",
                ephemeral=True,
            )
            return

        auth_type = "OAuth" if auth_method == AuthMethod.OAUTH else "API key"
        await ctx.respond(
            f"Starting Claude Code session ({auth_type}, stream mode)...",
            ephemeral=True,
        )

        try:
            # Start session
            session = await self.session_manager.start_session(user_id, auth_method)

            # Start code-server
            cs_info = await self.code_server_manager.start(user_id, session.workspace)
            session.code_server = cs_info

            # Store channel for this user
            self._user_channels[user_id] = ctx.channel

            # Create status embed
            embed = self.embed_manager.get_or_create(user_id, ctx.author.display_name)
            await embed.create_message(ctx.channel)

            await ctx.followup.send(
                f"Session started! Messages in this channel go to Claude Code.\n"
                f"**Code Editor:** {cs_info.url}\n"
                f"**Password:** ||{cs_info.password}||\n"
                "Use `/cc stop` to end the session.",
                ephemeral=True,
            )

        except Exception as e:
            logger.exception(f"Failed to start session for {user_id}")
            await ctx.followup.send(f"Failed to start session: {e}", ephemeral=True)

    async def stop_user_session(self, ctx: discord.ApplicationContext) -> None:
        """Stop a user's Claude Code session."""
        user_id = str(ctx.author.id)

        if not self.session_manager.has_session(user_id):
            await ctx.respond("You don't have an active session.", ephemeral=True)
            return

        # Stop code-server
        await self.code_server_manager.stop(user_id)

        # Stop session
        await self.session_manager.stop_session(user_id)

        # Remove channel mapping
        self._user_channels.pop(user_id, None)

        # Remove embed
        await self.embed_manager.remove(user_id)

        await ctx.respond("Session stopped.", ephemeral=True)

    async def close(self) -> None:
        """Clean up when bot is closing."""
        # Stop all code-servers
        await self.code_server_manager.stop_all()

        # Stop all sessions
        for user_id in list(self.session_manager.get_all_sessions().keys()):
            await self.session_manager.stop_session(user_id)

        await super().close()


# Create bot instance
bot = StreamCodesmithBot()

# Slash command group
cc_group = discord.SlashCommandGroup("cc", "Claude Code commands")


@cc_group.command(name="start", description="Start a new Claude Code session")
async def cc_start(ctx: discord.ApplicationContext):
    """Start a new Claude Code session."""
    await bot.start_user_session(ctx)


@cc_group.command(name="stop", description="Stop your Claude Code session")
async def cc_stop(ctx: discord.ApplicationContext):
    """Stop your Claude Code session."""
    await bot.stop_user_session(ctx)


@cc_group.command(name="cancel", description="Cancel the current Claude request")
async def cc_cancel(ctx: discord.ApplicationContext):
    """Cancel the current request."""
    user_id = str(ctx.author.id)
    session = bot.session_manager.get_session(user_id)

    if not session:
        await ctx.respond("No active session.", ephemeral=True)
        return

    if not session.is_busy:
        await ctx.respond("No active request to cancel.", ephemeral=True)
        return

    await session.runner.cancel()
    await ctx.respond("Request cancelled.", ephemeral=True)


@cc_group.command(name="status", description="Show session status")
async def cc_status(ctx: discord.ApplicationContext):
    """Show current session status."""
    user_id = str(ctx.author.id)
    info = bot.session_manager.get_session_info(user_id)

    if not info:
        await ctx.respond("No active session", ephemeral=True)
        return

    session_id = info.get("session_id")
    session_id_display = f"`{session_id[:8]}...`" if session_id else "None"

    await ctx.respond(
        f"**Session Status**\n"
        f"Model: {info.get('model') or 'Unknown'}\n"
        f"Tokens: {info.get('total_tokens', 0):,}\n"
        f"Cost: ${info.get('total_cost_usd', 0):.4f}\n"
        f"Session ID: {session_id_display}\n"
        f"Busy: {'Yes' if info.get('is_busy') else 'No'}",
        ephemeral=True,
    )


@cc_group.command(name="requirements", description="Check requirements")
async def cc_requirements(ctx: discord.ApplicationContext):
    """Check if all requirements are met."""
    reqs = check_requirements()
    lines = []
    for req, available in reqs.items():
        status = "✅" if available else "❌"
        lines.append(f"{status} {req}")

    await ctx.respond(
        "**Requirements**\n" + "\n".join(lines),
        ephemeral=True,
    )


@cc_group.command(name="code", description="Get link to code editor")
async def cc_code(ctx: discord.ApplicationContext):
    """Get the code-server URL for your session."""
    user_id = str(ctx.author.id)
    session = bot.session_manager.get_session(user_id)

    if not session:
        await ctx.respond("No active session.", ephemeral=True)
        return

    if not session.code_server:
        await ctx.respond("Code editor not available.", ephemeral=True)
        return

    await ctx.respond(
        f"**Code Editor:** {session.code_server.url}\n"
        f"**Password:** ||{session.code_server.password}||",
        ephemeral=True,
    )


@cc_group.command(
    name="login", description="Authenticate with your Claude Max/Pro subscription"
)
async def cc_login(ctx: discord.ApplicationContext):
    """Start OAuth credential setup via DM."""
    user_id = str(ctx.author.id)

    if has_valid_credentials(user_id):
        await ctx.respond(
            "You already have OAuth credentials stored. "
            "Use `/cc logout` first if you want to re-authenticate.",
            ephemeral=True,
        )
        return

    bot._pending_logins.add(user_id)

    await ctx.respond(
        "Check your DMs for authentication instructions.",
        ephemeral=True,
    )

    try:
        dm_channel = await ctx.author.create_dm()
        await dm_channel.send(
            "**Claude Max/Pro Authentication**\n\n"
            "To use your Claude Max/Pro subscription with Codesmith:\n\n"
            "1. Open a terminal on your computer\n"
            "2. Run: `claude login`\n"
            '3. Select **"Claude account with subscription"**\n'
            "4. Complete the login in your browser\n"
            "5. Run: `cat ~/.claude/.credentials.json`\n"
            "6. Copy the **entire JSON output** and paste it here\n\n"
            "Your credentials will be stored securely and used for your sessions."
        )
    except discord.Forbidden:
        bot._pending_logins.discard(user_id)
        await ctx.followup.send(
            "Could not send DM. Please enable DMs from server members.",
            ephemeral=True,
        )


@cc_group.command(name="logout", description="Remove your stored Claude credentials")
async def cc_logout(ctx: discord.ApplicationContext):
    """Remove stored OAuth credentials."""
    user_id = str(ctx.author.id)

    if delete_credentials(user_id):
        await ctx.respond(
            "Your Claude credentials have been removed.",
            ephemeral=True,
        )
    else:
        await ctx.respond(
            "No stored credentials to remove.",
            ephemeral=True,
        )


bot.add_application_command(cc_group)


def main():
    """Main entry point."""
    if not is_configured():
        logger.error("Missing required configuration!")
        logger.error("Set DISCORD_BOT_TOKEN in environment")
        return

    logger.info("Starting Stream Codesmith bot...")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
