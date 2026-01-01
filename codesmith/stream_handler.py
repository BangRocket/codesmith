"""Stream-based session handler for Claude Code using --output-format stream-json.

This replaces the PTY-based approach with direct JSON streaming, avoiding
terminal emulation complexity.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .auth import AuthMethod
from .config import ANTHROPIC_API_KEY, ensure_workspace


@dataclass
class StreamEvent:
    """A parsed event from Claude Code's stream-json output.

    Event types include:
    - system: System messages (init, config)
    - assistant: Claude's response text
    - user: Echo of user input
    - result: Final result with metadata
    - error: Error messages
    """

    type: str
    content: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # Metadata extracted from result events
    session_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class StreamSession:
    """Manages a stream-json based Claude Code session.

    Unlike PTYSession, this runs Claude Code in non-interactive mode with
    --output-format stream-json, parsing JSON events directly.

    Attributes:
        user_id: User identifier for this session
        workspace: Path to user's workspace
        session_id: Claude Code session ID for continuity
        auth_method: Authentication method (OAUTH or API_KEY)
        process: Current running subprocess (if any)
        created_at: When the session was created
        last_activity: Last time input/output occurred
    """

    user_id: str
    workspace: Path
    auth_method: AuthMethod = AuthMethod.API_KEY
    session_id: str | None = None
    process: asyncio.subprocess.Process | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

    # Accumulated stats
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    model: str | None = None

    def is_busy(self) -> bool:
        """Check if a request is currently being processed."""
        return self.process is not None and self.process.returncode is None

    def _build_command(self, prompt: str, continue_session: bool = True) -> list[str]:
        """Build the Claude Code command for a request.

        Args:
            prompt: The user's prompt/message
            continue_session: Whether to continue existing session

        Returns:
            Command arguments list
        """
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]

        # Continue previous session if we have one
        if continue_session and self.session_id:
            cmd.extend(["--continue", self.session_id])

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the subprocess."""
        import os

        env = os.environ.copy()

        # Set working directory context
        env["HOME"] = str(self.workspace / ".home")

        # Set API key if using API key auth
        if self.auth_method == AuthMethod.API_KEY and ANTHROPIC_API_KEY:
            env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

        return env

    async def send_message(
        self,
        message: str,
        on_event: Callable[[StreamEvent], None] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send a message and stream back events.

        Args:
            message: User message to send
            on_event: Optional callback for each event

        Yields:
            StreamEvent objects as they arrive
        """
        if self.is_busy():
            raise RuntimeError("Session is busy processing another request")

        cmd = self._build_command(message)
        env = self._build_env()

        # Ensure home directory exists
        home_dir = self.workspace / ".home"
        home_dir.mkdir(exist_ok=True)

        # Link .claude directory if it exists
        claude_dir = self.workspace / ".claude"
        home_claude_dir = home_dir / ".claude"
        if claude_dir.exists() and not home_claude_dir.exists():
            home_claude_dir.symlink_to(claude_dir)

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
            env=env,
        )

        self.last_activity = datetime.now()

        try:
            async for event in self._read_events():
                self.last_activity = datetime.now()

                # Update session state from result events
                if event.type == "result" and event.session_id:
                    self.session_id = event.session_id
                    if event.model:
                        self.model = event.model
                    self.total_input_tokens += event.input_tokens
                    self.total_output_tokens += event.output_tokens
                    self.total_cost_usd += event.cost_usd

                if on_event:
                    try:
                        on_event(event)
                    except Exception:
                        pass  # Don't let callback errors stop the stream

                yield event

        finally:
            self.process = None

    async def _read_events(self) -> AsyncIterator[StreamEvent]:
        """Read and parse events from the subprocess stdout.

        Yields:
            Parsed StreamEvent objects
        """
        if not self.process or not self.process.stdout:
            return

        buffer = ""

        while True:
            try:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8", errors="replace")

                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if not line:
                        continue

                    event = self._parse_event(line)
                    if event:
                        yield event

            except asyncio.CancelledError:
                raise
            except Exception:
                break

        # Wait for process to complete
        await self.process.wait()

        # Check for stderr errors
        if self.process.stderr:
            stderr = await self.process.stderr.read()
            if stderr:
                yield StreamEvent(
                    type="error",
                    content=stderr.decode("utf-8", errors="replace"),
                )

    def _parse_event(self, line: str) -> StreamEvent | None:
        """Parse a JSON line into a StreamEvent.

        Args:
            line: JSON string to parse

        Returns:
            StreamEvent or None if parsing fails
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "unknown")
        content = ""

        # Extract content based on event type
        if event_type == "assistant":
            # Assistant message content
            message = data.get("message", {})
            if isinstance(message, dict):
                content_blocks = message.get("content", [])
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content += block.get("text", "")
            elif isinstance(message, str):
                content = message

        elif event_type == "content_block_delta":
            # Streaming delta for partial content
            delta = data.get("delta", {})
            content = delta.get("text", "")

        elif event_type == "result":
            # Final result with metadata
            content = data.get("result", "")

        elif event_type == "error":
            content = data.get("error", {}).get("message", str(data.get("error", "")))

        elif event_type == "system":
            content = data.get("message", "")

        event = StreamEvent(
            type=event_type,
            content=content,
            raw=data,
        )

        # Extract metadata from result events
        if event_type == "result":
            event.session_id = data.get("session_id")

            usage = data.get("usage", {})
            event.input_tokens = usage.get("input_tokens", 0)
            event.output_tokens = usage.get("output_tokens", 0)

            # Cost might be in different places
            event.cost_usd = data.get("cost_usd", 0.0)
            event.model = data.get("model")

        return event

    async def cancel(self) -> None:
        """Cancel the current request if running."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except TimeoutError:
                self.process.kill()
            self.process = None

    def get_stats(self) -> dict[str, Any]:
        """Get session statistics.

        Returns:
            Dict with session stats
        """
        return {
            "session_id": self.session_id,
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "is_busy": self.is_busy(),
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }


async def create_stream_session(
    user_id: str,
    auth_method: AuthMethod = AuthMethod.API_KEY,
) -> StreamSession:
    """Create a new stream-based session.

    Args:
        user_id: User identifier
        auth_method: Authentication method

    Returns:
        New StreamSession
    """
    workspace = ensure_workspace(user_id)

    return StreamSession(
        user_id=user_id,
        workspace=workspace,
        auth_method=auth_method,
    )
