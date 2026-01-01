"""Stream output parser for Claude Code stream-json events.

Handles buffering, formatting, and Discord chunking for stream-json output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .output_parser import chunk_for_discord

if TYPE_CHECKING:
    from .stream_handler import StreamEvent


@dataclass
class StreamBuffer:
    """Buffer for accumulating stream events into Discord messages.

    Handles:
    - Accumulating text deltas into complete messages
    - Tracking when to flush (send) accumulated content
    - Formatting content for Discord
    """

    _content_buffer: str = ""
    _last_flush: str = ""
    _is_streaming: bool = False

    # Configurable thresholds
    flush_threshold: int = field(default=500)  # Flush when buffer exceeds this
    min_flush_interval: float = field(default=0.5)  # Min seconds between flushes

    def feed(self, event: StreamEvent) -> str | None:
        """Feed an event and return content to send if ready.

        Args:
            event: StreamEvent to process

        Returns:
            Content string to send, or None if buffering
        """
        if event.type == "assistant":
            # Full assistant message - return it
            self._is_streaming = False
            if event.content:
                self._content_buffer = ""
                return event.content

        elif event.type == "content_block_delta":
            # Streaming delta - accumulate
            self._is_streaming = True
            self._content_buffer += event.content

            # Check if we should flush
            if len(self._content_buffer) >= self.flush_threshold:
                return self._flush()

        elif event.type == "result":
            # End of response - flush remaining
            self._is_streaming = False
            if self._content_buffer:
                return self._flush()

        elif event.type == "error":
            # Error - return immediately
            self._is_streaming = False
            return f"**Error:** {event.content}"

        return None

    def _flush(self) -> str:
        """Flush the buffer and return its contents."""
        content = self._content_buffer
        self._last_flush = content
        self._content_buffer = ""
        return content

    def get_pending(self) -> str:
        """Get any pending (unflushed) content.

        Returns:
            Buffered content that hasn't been sent yet
        """
        return self._content_buffer

    def is_streaming(self) -> bool:
        """Check if currently receiving streaming content."""
        return self._is_streaming

    def clear(self) -> None:
        """Clear the buffer."""
        self._content_buffer = ""
        self._last_flush = ""
        self._is_streaming = False


@dataclass
class StatusInfo:
    """Extracted status information from stream events."""

    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    session_id: str | None = None

    def format_statusbar(self) -> str:
        """Format status info as a statusbar-like string.

        Returns:
            Formatted status string
        """
        parts = []

        if self.model:
            parts.append(self.model)

        total_tokens = self.input_tokens + self.output_tokens
        if total_tokens:
            parts.append(f"{total_tokens:,} tokens")

        if self.cost_usd > 0:
            parts.append(f"${self.cost_usd:.4f}")

        return " | ".join(parts) if parts else ""

    @classmethod
    def from_event(cls, event: StreamEvent) -> StatusInfo:
        """Create StatusInfo from a result event.

        Args:
            event: StreamEvent (should be type='result')

        Returns:
            StatusInfo instance
        """
        return cls(
            model=event.model,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cost_usd=event.cost_usd,
            session_id=event.session_id,
        )


def format_event_for_discord(event: StreamEvent) -> str | None:
    """Format a single event for Discord display.

    Args:
        event: StreamEvent to format

    Returns:
        Formatted string or None if event shouldn't be displayed
    """
    if event.type == "assistant" and event.content:
        return event.content

    elif event.type == "content_block_delta" and event.content:
        return event.content

    elif event.type == "error":
        return f"**Error:** {event.content}"

    elif event.type == "system" and event.content:
        return f"*{event.content}*"

    return None


def format_tool_use(event: StreamEvent) -> str | None:
    """Format tool use events for Discord.

    Args:
        event: StreamEvent that might contain tool use

    Returns:
        Formatted tool use string or None
    """
    raw = event.raw

    # Check for tool use in the message
    if event.type == "assistant":
        message = raw.get("message", {})
        content_blocks = message.get("content", []) if isinstance(message, dict) else []

        tool_uses = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_uses.append(f"`{tool_name}`")

        if tool_uses:
            return f"*Using tools: {', '.join(tool_uses)}*"

    return None


class StreamOutputFormatter:
    """Formats stream events for Discord output.

    Handles:
    - Buffering streaming deltas
    - Chunking large outputs for Discord limits
    - Formatting tool use notifications
    - Tracking status info
    """

    def __init__(self):
        """Initialize the formatter."""
        self.buffer = StreamBuffer()
        self.status = StatusInfo()
        self._pending_chunks: list[str] = []

    def feed(self, event: StreamEvent) -> list[str]:
        """Feed an event and return chunks to send.

        Args:
            event: StreamEvent to process

        Returns:
            List of message chunks to send to Discord
        """
        chunks = []

        # Check for tool use
        tool_msg = format_tool_use(event)
        if tool_msg:
            chunks.append(tool_msg)

        # Get content from buffer
        content = self.buffer.feed(event)
        if content:
            # Chunk for Discord limits
            chunks.extend(chunk_for_discord(content))

        # Update status from result events
        if event.type == "result":
            self.status = StatusInfo.from_event(event)

        return chunks

    def get_pending(self) -> str:
        """Get any pending content not yet returned."""
        return self.buffer.get_pending()

    def flush(self) -> list[str]:
        """Force flush any pending content.

        Returns:
            List of message chunks
        """
        pending = self.buffer.get_pending()
        self.buffer.clear()
        if pending:
            return chunk_for_discord(pending)
        return []

    def get_status(self) -> StatusInfo:
        """Get current status info."""
        return self.status

    def reset(self) -> None:
        """Reset the formatter for a new response."""
        self.buffer.clear()
        # Keep status - it accumulates across messages
