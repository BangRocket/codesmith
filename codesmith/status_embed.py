"""Discord embed for Claude Code status display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord


@dataclass
class StatusData:
    """Parsed status data from Claude Code statusbar.

    Attributes:
        model: Model name (e.g., "Claude Sonnet 4")
        input_tokens: Input token count
        output_tokens: Output token count
        cost: Cost in USD
        context_percent: Context window usage percentage
        raw: Raw statusbar text
    """

    model: str = "Unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    context_percent: float = 0.0
    raw: str = ""


def parse_statusbar(statusbar: str | None) -> StatusData:
    """Parse Claude Code statusbar text into structured data.

    Args:
        statusbar: Raw statusbar text from terminal

    Returns:
        StatusData with parsed fields
    """
    if not statusbar:
        return StatusData()

    data = StatusData(raw=statusbar)

    # Try to extract model name
    # Common patterns: "Sonnet", "Opus", "Haiku", "claude-..."
    model_patterns = [
        r"(Claude\s+(?:Sonnet|Opus|Haiku)[^,\s]*)",
        r"(sonnet[^,\s]*)",
        r"(opus[^,\s]*)",
        r"(haiku[^,\s]*)",
        r"(claude-[^\s,]+)",
    ]
    for pattern in model_patterns:
        match = re.search(pattern, statusbar, re.IGNORECASE)
        if match:
            data.model = match.group(1)
            break

    # Extract token counts
    # Patterns: "12.3k tokens", "1,234 tokens", "input: 1234"
    token_match = re.search(
        r"(\d+[,.]?\d*)\s*k?\s*(?:input\s+)?tokens?",
        statusbar,
        re.IGNORECASE,
    )
    if token_match:
        token_str = token_match.group(1).replace(",", "")
        match_text = statusbar[token_match.start():token_match.end()].lower()
        multiplier = 1000 if "k" in match_text else 1
        try:
            data.input_tokens = int(float(token_str) * multiplier)
        except ValueError:
            pass

    # Extract cost
    # Patterns: "$0.0123", "cost: $1.23"
    cost_match = re.search(r"\$(\d+\.?\d*)", statusbar)
    if cost_match:
        try:
            data.cost = float(cost_match.group(1))
        except ValueError:
            pass

    # Extract context percentage
    # Patterns: "78%", "context: 78%"
    percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", statusbar)
    if percent_match:
        try:
            data.context_percent = float(percent_match.group(1))
        except ValueError:
            pass

    return data


def create_progress_bar(percent: float, length: int = 10) -> str:
    """Create a text-based progress bar.

    Args:
        percent: Percentage (0-100)
        length: Number of characters for the bar

    Returns:
        Progress bar string like "████████░░"
    """
    filled = int(percent / 100 * length)
    empty = length - filled
    return "█" * filled + "░" * empty


class StatusEmbed:
    """Manages the Discord status embed for a Claude Code session."""

    def __init__(self, user_id: str, username: str = "User"):
        """Initialize status embed.

        Args:
            user_id: Discord user ID
            username: Discord username for display
        """
        self.user_id = user_id
        self.username = username
        self.status_data = StatusData()
        self.session_start: datetime = datetime.now()
        self.last_update: datetime = datetime.now()
        self._message: discord.Message | None = None

    def update_status(self, statusbar: str | None) -> None:
        """Update status from new statusbar text.

        Args:
            statusbar: Raw statusbar text
        """
        if statusbar:
            self.status_data = parse_statusbar(statusbar)
            self.last_update = datetime.now()

    def build_embed(self) -> dict:
        """Build the Discord embed dictionary.

        Returns:
            Dict suitable for discord.Embed.from_dict()
        """
        uptime = datetime.now() - self.session_start
        uptime_str = self._format_duration(uptime.total_seconds())

        # Build progress bar for context
        progress = create_progress_bar(self.status_data.context_percent)

        # Determine status color based on context usage
        if self.status_data.context_percent > 90:
            color = 0xF04747  # Red - critical
        elif self.status_data.context_percent > 70:
            color = 0xFAA61A  # Yellow - warning
        else:
            color = 0x43B581  # Green - good

        fields = [
            {
                "name": "Model",
                "value": self.status_data.model,
                "inline": True,
            },
            {
                "name": "Input Tokens",
                "value": f"{self.status_data.input_tokens:,}",
                "inline": True,
            },
            {
                "name": "Cost",
                "value": f"${self.status_data.cost:.4f}",
                "inline": True,
            },
            {
                "name": "Context Usage",
                "value": f"{progress} {self.status_data.context_percent:.0f}%",
                "inline": False,
            },
            {
                "name": "Session",
                "value": f"Active ({uptime_str})",
                "inline": True,
            },
        ]

        return {
            "title": "Claude Code Status",
            "description": f"Session for {self.username}",
            "color": color,
            "fields": fields,
            "footer": {
                "text": "Last updated"
            },
            "timestamp": self.last_update.isoformat(),
        }

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted string like "5m 23s" or "1h 30m"
        """
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"

    async def create_message(self, channel) -> None:
        """Create and pin the status embed in a channel.

        Args:
            channel: Discord channel to send to
        """
        import discord

        embed = discord.Embed.from_dict(self.build_embed())
        self._message = await channel.send(embed=embed)

        # Try to pin the message
        try:
            await self._message.pin()
        except discord.Forbidden:
            pass  # No permission to pin

    async def update_message(self) -> None:
        """Update the existing status message."""
        if self._message is None:
            return

        import discord

        try:
            embed = discord.Embed.from_dict(self.build_embed())
            await self._message.edit(embed=embed)
        except discord.NotFound:
            self._message = None
        except discord.HTTPException:
            pass  # Rate limited or other error

    async def delete_message(self) -> None:
        """Delete the status message."""
        if self._message is None:
            return

        try:
            # Unpin first
            try:
                await self._message.unpin()
            except Exception:
                pass

            await self._message.delete()
        except Exception:
            pass
        finally:
            self._message = None

    @property
    def message(self):
        """Get the Discord message object."""
        return self._message


class EmbedManager:
    """Manages status embeds for all users."""

    def __init__(self):
        """Initialize embed manager."""
        self._embeds: dict[str, StatusEmbed] = {}

    def get_or_create(self, user_id: str, username: str = "User") -> StatusEmbed:
        """Get or create a status embed for a user.

        Args:
            user_id: Discord user ID
            username: Discord username

        Returns:
            StatusEmbed for the user
        """
        if user_id not in self._embeds:
            self._embeds[user_id] = StatusEmbed(user_id, username)
        return self._embeds[user_id]

    def get(self, user_id: str) -> StatusEmbed | None:
        """Get a user's status embed if it exists.

        Args:
            user_id: Discord user ID

        Returns:
            StatusEmbed or None
        """
        return self._embeds.get(user_id)

    async def remove(self, user_id: str) -> None:
        """Remove and clean up a user's status embed.

        Args:
            user_id: Discord user ID
        """
        embed = self._embeds.pop(user_id, None)
        if embed:
            await embed.delete_message()

    def update_status(self, user_id: str, statusbar: str | None) -> None:
        """Update status for a user's embed.

        Args:
            user_id: Discord user ID
            statusbar: Raw statusbar text
        """
        embed = self._embeds.get(user_id)
        if embed:
            embed.update_status(statusbar)
