"""Codesmith Discord bot - Claude Code to Discord bridge."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

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
from .config import (
    CODESMITH_CHANNEL_ID,
    DISCORD_BOT_TOKEN,
    EMBED_UPDATE_INTERVAL,
    is_configured,
)
from .output_parser import ParsedOutput, chunk_for_discord, format_for_discord
from .sandbox_runner import check_sandbox_requirements
from .session_manager import SessionManager, get_session_manager
from .status_embed import EmbedManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("codesmith")

# Intents for the bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


class CodesmithBot(commands.Bot):
    """Discord bot that bridges Claude Code sessions."""

    def __init__(self):
        """Initialize the bot."""
        super().__init__(
            command_prefix="!",  # Not really used, we use slash commands
            intents=intents,
        )
        self.session_manager: SessionManager = get_session_manager()
        self.embed_manager = EmbedManager()
        self._output_queues: dict[str, asyncio.Queue] = {}
        self._output_tasks: dict[str, asyncio.Task] = {}
        self._pending_logins: set[str] = set()  # User IDs awaiting credential paste

    async def setup_hook(self) -> None:
        """Called when the bot is starting up."""
        # Register slash commands
        self.add_application_command(cc_group)

        # Start session manager
        await self.session_manager.start()

        # Start embed update loop
        self.update_embeds.start()

        logger.info("Codesmith bot initialized")

    async def on_ready(self) -> None:
        """Called when the bot is connected and ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")

        # Check sandbox requirements
        reqs = check_sandbox_requirements()
        for req, available in reqs.items():
            status = "OK" if available else "MISSING"
            logger.info(f"  {req}: {status}")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        # Ignore bot messages
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
        if not self.session_manager.has_session(user_id):
            return  # No session, ignore regular messages

        # Send message content to Claude Code
        success = await self.session_manager.send_input(user_id, message.content)
        if success:
            # Add reaction to indicate message was sent
            try:
                await message.add_reaction("📨")
            except discord.Forbidden:
                pass

    async def _handle_credential_paste(self, message: discord.Message) -> None:
        """Handle credential JSON pasted in DM.

        Args:
            message: Discord DM message
        """
        user_id = str(message.author.id)
        content = message.content.strip()

        # Validate the JSON
        credentials = validate_credentials_json(content)

        if credentials is None:
            await message.channel.send(
                "Invalid credentials format. Please paste the entire contents of "
                "`~/.claude/.credentials.json` (should be valid JSON with "
                "`claudeAiOauth` key)."
            )
            return

        # Store credentials
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

    def _get_output_callback(self, channel: discord.TextChannel):
        """Create an output callback for a channel.

        Args:
            channel: Discord channel to send output to

        Returns:
            Callback function
        """

        def callback(user_id: str, parsed: ParsedOutput) -> None:
            # Queue output for processing
            if user_id not in self._output_queues:
                self._output_queues[user_id] = asyncio.Queue()

            self._output_queues[user_id].put_nowait((channel, parsed))

            # Update statusbar in embed
            if parsed.statusbar:
                self.embed_manager.update_status(user_id, parsed.statusbar)

        return callback

    async def _process_output_queue(self, user_id: str) -> None:
        """Process queued output for a user.

        Args:
            user_id: User ID
        """
        queue = self._output_queues.get(user_id)
        if not queue:
            return

        buffer = ""
        last_send = datetime.now()

        while True:
            try:
                # Wait for output with timeout
                channel, parsed = await asyncio.wait_for(
                    queue.get(), timeout=0.5
                )

                if parsed.content:
                    buffer += parsed.content

                # Send if buffer is getting large or enough time passed
                elapsed = (datetime.now() - last_send).total_seconds()
                if len(buffer) > 1500 or (buffer and elapsed > 1.0):
                    await self._send_output(channel, buffer)
                    buffer = ""
                    last_send = datetime.now()

            except TimeoutError:
                # Timeout - send any remaining buffer
                if buffer:
                    channel = None
                    # Try to get channel from last message
                    if user_id in self._output_queues:
                        try:
                            channel, _ = self._output_queues[user_id].get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    if channel:
                        await self._send_output(channel, buffer)
                    buffer = ""
                    last_send = datetime.now()

            except asyncio.CancelledError:
                break

    async def _send_output(
        self, channel: discord.TextChannel, content: str
    ) -> None:
        """Send output to Discord channel.

        Args:
            channel: Channel to send to
            content: Content to send
        """
        if not content.strip():
            return

        # Format and chunk for Discord
        formatted = format_for_discord(content)
        chunks = chunk_for_discord(formatted)

        for chunk in chunks:
            try:
                await channel.send(chunk)
            except discord.HTTPException as e:
                logger.error(f"Failed to send message: {e}")
                break

            # Small delay between chunks
            if len(chunks) > 1:
                await asyncio.sleep(0.5)

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
        resume: bool = False,
    ) -> None:
        """Start a Claude Code session for a user.

        Args:
            ctx: Discord application context
            resume: Whether to resume previous session
        """
        user_id = str(ctx.author.id)

        # Check requirements
        reqs = check_sandbox_requirements()
        missing = [k for k, v in reqs.items() if not v]
        if missing:
            await ctx.respond(
                f"Cannot start session. Missing requirements: {', '.join(missing)}",
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
            f"Starting Claude Code session ({auth_type})...", ephemeral=True
        )

        try:
            # Start session
            await self.session_manager.start_session(user_id)

            # Set up output callback
            callback = self._get_output_callback(ctx.channel)
            self.session_manager.set_output_callback(user_id, callback)

            # Start output processing task
            self._output_queues[user_id] = asyncio.Queue()
            self._output_tasks[user_id] = asyncio.create_task(
                self._process_output_queue(user_id)
            )

            # Create status embed
            embed = self.embed_manager.get_or_create(
                user_id, ctx.author.display_name
            )
            await embed.create_message(ctx.channel)

            await ctx.followup.send(
                "Session started! Messages in this channel go to Claude Code.\n"
                "Use `/cc stop` to end the session.",
                ephemeral=True,
            )

        except Exception as e:
            logger.exception(f"Failed to start session for {user_id}")
            await ctx.followup.send(
                f"Failed to start session: {e}",
                ephemeral=True,
            )

    async def stop_user_session(self, ctx: discord.ApplicationContext) -> None:
        """Stop a user's Claude Code session.

        Args:
            ctx: Discord application context
        """
        user_id = str(ctx.author.id)

        if not self.session_manager.has_session(user_id):
            await ctx.respond("You don't have an active session.", ephemeral=True)
            return

        # Cancel output task
        task = self._output_tasks.pop(user_id, None)
        if task:
            task.cancel()

        # Remove queue
        self._output_queues.pop(user_id, None)

        # Stop session
        await self.session_manager.stop_session(user_id)

        # Remove embed
        await self.embed_manager.remove(user_id)

        await ctx.respond("Session stopped.", ephemeral=True)

    async def close(self) -> None:
        """Clean up when bot is closing."""
        # Stop all sessions
        await self.session_manager.stop()

        # Cancel all output tasks
        for task in self._output_tasks.values():
            task.cancel()

        await super().close()


# Create bot instance
bot = CodesmithBot()

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


@cc_group.command(name="clear", description="Clear Claude Code conversation history")
async def cc_clear(ctx: discord.ApplicationContext):
    """Send /clear to Claude Code."""
    user_id = str(ctx.author.id)
    if await bot.session_manager.send_slash_command(user_id, "/clear"):
        await ctx.respond("Sent /clear to Claude Code", ephemeral=True)
    else:
        await ctx.respond("No active session", ephemeral=True)


@cc_group.command(name="compact", description="Compact Claude Code conversation")
async def cc_compact(ctx: discord.ApplicationContext):
    """Send /compact to Claude Code."""
    user_id = str(ctx.author.id)
    if await bot.session_manager.send_slash_command(user_id, "/compact"):
        await ctx.respond("Sent /compact to Claude Code", ephemeral=True)
    else:
        await ctx.respond("No active session", ephemeral=True)


@cc_group.command(name="model", description="Change Claude Code model")
async def cc_model(
    ctx: discord.ApplicationContext,
    model: discord.Option(str, "Model name (e.g., sonnet, opus, haiku)"),
):
    """Send /model command to Claude Code."""
    user_id = str(ctx.author.id)
    if await bot.session_manager.send_slash_command(user_id, f"/model {model}"):
        await ctx.respond(f"Sent /model {model} to Claude Code", ephemeral=True)
    else:
        await ctx.respond("No active session", ephemeral=True)


@cc_group.command(name="status", description="Show session status")
async def cc_status(ctx: discord.ApplicationContext):
    """Show current session status."""
    user_id = str(ctx.author.id)
    info = bot.session_manager.get_session_info(user_id)

    if not info:
        await ctx.respond("No active session", ephemeral=True)
        return

    uptime = int(info["uptime_seconds"])
    idle = int(info["idle_seconds"])

    await ctx.respond(
        f"**Session Status**\n"
        f"Uptime: {uptime // 60}m {uptime % 60}s\n"
        f"Idle: {idle}s\n"
        f"Workspace: `{info['workspace']}`\n"
        f"Status: {'Active' if info['alive'] else 'Stopped'}",
        ephemeral=True,
    )


@cc_group.command(name="requirements", description="Check sandbox requirements")
async def cc_requirements(ctx: discord.ApplicationContext):
    """Check if all requirements are met."""
    reqs = check_sandbox_requirements()
    lines = []
    for req, available in reqs.items():
        status = "✅" if available else "❌"
        lines.append(f"{status} {req}")

    await ctx.respond(
        "**Sandbox Requirements**\n" + "\n".join(lines),
        ephemeral=True,
    )


@cc_group.command(
    name="login", description="Authenticate with your Claude Max/Pro subscription"
)
async def cc_login(ctx: discord.ApplicationContext):
    """Start OAuth credential setup via DM."""
    user_id = str(ctx.author.id)

    # Check if already authenticated
    if has_valid_credentials(user_id):
        await ctx.respond(
            "You already have OAuth credentials stored. "
            "Use `/cc logout` first if you want to re-authenticate.",
            ephemeral=True,
        )
        return

    # Add to pending logins
    bot._pending_logins.add(user_id)

    # Send instructions in channel
    await ctx.respond(
        "Check your DMs for authentication instructions.",
        ephemeral=True,
    )

    # DM the user with instructions
    try:
        dm_channel = await ctx.author.create_dm()
        await dm_channel.send(
            "**Claude Max/Pro Authentication**\n\n"
            "To use your Claude Max/Pro subscription with Codesmith:\n\n"
            "1. Open a terminal on your computer\n"
            "2. Run: `claude login`\n"
            "3. Select **\"Claude account with subscription\"**\n"
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


# Add command group to bot
bot.add_application_command(cc_group)


def main():
    """Main entry point."""
    if not is_configured():
        logger.error("Missing required configuration!")
        logger.error("Set DISCORD_BOT_TOKEN in environment")
        return

    logger.info("Starting Codesmith bot...")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
