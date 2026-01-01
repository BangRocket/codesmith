"""Output parser for Claude Code terminal output.

Handles ANSI escape code parsing, statusbar extraction, and Discord chunking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pyte

from .config import DISCORD_MSG_LIMIT, PTY_COLS, PTY_ROWS

# ANSI escape sequence patterns
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
ANSI_OSC = re.compile(r"\x1b\][^\x07]*\x07")  # Operating System Commands
ANSI_CURSOR_SAVE = re.compile(r"\x1b7|\x1b\[s")
ANSI_CURSOR_RESTORE = re.compile(r"\x1b8|\x1b\[u")
ANSI_CURSOR_POSITION = re.compile(r"\x1b\[(\d+);(\d+)H")


@dataclass
class ParsedOutput:
    """Parsed terminal output.

    Attributes:
        content: Main content text (ANSI stripped)
        statusbar: Extracted statusbar text if present
        raw: Original raw output
    """

    content: str
    statusbar: str | None = None
    raw: bytes = b""


class TerminalEmulator:
    """Terminal emulator using pyte for accurate screen state tracking."""

    def __init__(self, cols: int = PTY_COLS, rows: int = PTY_ROWS):
        """Initialize terminal emulator.

        Args:
            cols: Terminal width
            rows: Terminal height
        """
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)

    def feed(self, data: bytes) -> None:
        """Feed raw terminal data to the emulator.

        Args:
            data: Raw bytes from PTY
        """
        try:
            text = data.decode("utf-8", errors="replace")
            self.stream.feed(text)
        except Exception:
            pass

    def get_display(self) -> list[str]:
        """Get current screen content as lines.

        Returns:
            List of screen lines
        """
        return list(self.screen.display)

    def get_content(self) -> str:
        """Get screen content as single string (trailing whitespace stripped).

        Returns:
            Screen content
        """
        lines = [line.rstrip() for line in self.screen.display]
        # Remove trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def get_statusbar(self) -> str | None:
        """Extract statusbar from bottom of screen.

        Claude Code's statusbar is typically on the last line.

        Returns:
            Statusbar text or None
        """
        lines = self.screen.display
        if lines:
            last_line = lines[-1].strip()
            # Check if it looks like a statusbar (has common indicators)
            if any(
                x in last_line.lower()
                for x in [
                    "token",
                    "cost",
                    "model",
                    "context",
                    "$",
                    "sonnet",
                    "opus",
                    "haiku",
                ]
            ):
                return last_line
        return None

    def reset(self) -> None:
        """Reset the terminal state."""
        self.screen.reset()


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Args:
        text: Text potentially containing ANSI codes

    Returns:
        Clean text without ANSI codes
    """
    result = ANSI_ESCAPE.sub("", text)
    result = ANSI_OSC.sub("", result)
    return result


def parse_output(data: bytes) -> ParsedOutput:
    """Parse raw terminal output into structured data.

    Args:
        data: Raw bytes from PTY

    Returns:
        ParsedOutput with content and statusbar
    """
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return ParsedOutput(content="", raw=data)

    # Strip ANSI codes for content
    clean_text = strip_ansi(text)

    # Try to extract statusbar (usually at the end after cursor positioning)
    statusbar = None
    cursor_match = list(ANSI_CURSOR_POSITION.finditer(text))
    if cursor_match:
        # Last cursor position might indicate statusbar location
        last_match = cursor_match[-1]
        row = int(last_match.group(1))
        # If cursor moved to bottom area, text after might be statusbar
        if row > PTY_ROWS - 5:  # Bottom 5 rows
            after_cursor = text[last_match.end() :]
            statusbar_text = strip_ansi(after_cursor).strip()
            if statusbar_text:
                statusbar = statusbar_text
                # Remove statusbar from main content
                clean_text = clean_text.replace(statusbar_text, "").strip()

    return ParsedOutput(
        content=clean_text.strip(),
        statusbar=statusbar,
        raw=data,
    )


def chunk_for_discord(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """Split text into Discord-safe chunks.

    Tries to split at natural boundaries (code blocks, paragraphs, lines).

    Args:
        text: Text to split
        limit: Maximum chunk size (default: Discord limit)

    Returns:
        List of text chunks
    """
    if len(text) <= limit:
        return [text] if text else []

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Find best split point within limit
        chunk = remaining[:limit]
        split_point = limit

        # Try to split at code block boundary first
        code_block_end = chunk.rfind("```\n")
        if code_block_end > limit // 2:
            split_point = code_block_end + 4
        else:
            # Try paragraph break
            para_break = chunk.rfind("\n\n")
            if para_break > limit // 2:
                split_point = para_break + 2
            else:
                # Try line break
                line_break = chunk.rfind("\n")
                if line_break > limit // 2:
                    split_point = line_break + 1
                else:
                    # Try space
                    space = chunk.rfind(" ")
                    if space > limit // 2:
                        split_point = space + 1

        # Handle unclosed code blocks
        chunk_text = remaining[:split_point]
        code_opens = chunk_text.count("```")
        if code_opens % 2 == 1:  # Odd number means unclosed
            # Add closing backticks
            chunk_text = chunk_text.rstrip() + "\n```"
            # Add opening for next chunk
            remaining = "```\n" + remaining[split_point:]
        else:
            remaining = remaining[split_point:]

        chunks.append(chunk_text)

    return chunks


def format_for_discord(text: str) -> str:
    """Format terminal output for Discord display.

    Wraps output in code blocks if it looks like code/terminal output.

    Args:
        text: Text to format

    Returns:
        Discord-formatted text
    """
    if not text:
        return ""

    # Check if already has code blocks
    if text.startswith("```"):
        return text

    # Check if it looks like terminal output
    lines = text.split("\n")
    looks_like_terminal = any(
        line.startswith(("$", ">", ">>>", "#", "claude>"))
        for line in lines[:5]  # Check first few lines
    )

    if looks_like_terminal or len(lines) > 3:
        # Wrap in code block
        return f"```\n{text}\n```"

    return text


class OutputBuffer:
    """Buffer for accumulating and processing terminal output."""

    def __init__(self):
        """Initialize output buffer."""
        self._buffer = b""
        self._emulator = TerminalEmulator()
        self._last_content = ""
        self._last_statusbar: str | None = None

    def feed(self, data: bytes) -> ParsedOutput:
        """Feed data to buffer and get parsed output.

        Args:
            data: Raw bytes from PTY

        Returns:
            Parsed output with new content only (delta)
        """
        self._buffer += data
        self._emulator.feed(data)

        # Get current screen state
        current_content = self._emulator.get_content()
        current_statusbar = self._emulator.get_statusbar()

        # Calculate delta (new content only)
        if current_content.startswith(self._last_content):
            new_content = current_content[len(self._last_content) :]
        else:
            new_content = current_content

        self._last_content = current_content
        self._last_statusbar = current_statusbar

        return ParsedOutput(
            content=new_content.strip(),
            statusbar=current_statusbar,
            raw=data,
        )

    def get_full_content(self) -> str:
        """Get all accumulated content.

        Returns:
            Full content string
        """
        return self._last_content

    def get_statusbar(self) -> str | None:
        """Get current statusbar.

        Returns:
            Statusbar text or None
        """
        return self._last_statusbar

    def clear(self) -> None:
        """Clear the buffer."""
        self._buffer = b""
        self._emulator.reset()
        self._last_content = ""
        self._last_statusbar = None
