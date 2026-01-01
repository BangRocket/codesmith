"""Session manager for Claude Code user sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from .auth import AuthMethod, get_auth_method
from .config import SESSION_TIMEOUT
from .output_parser import OutputBuffer, ParsedOutput
from .pty_handler import PTYSession, create_pty_session
from .sandbox_runner import cleanup_sandbox, setup_sandbox

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class UserSession:
    """A user's Claude Code session.

    Attributes:
        user_id: Discord user ID
        pty: PTY session for Claude Code
        workspace: Path to user's workspace
        output_buffer: Buffer for parsing output
        created_at: When session was created
        claude_session_id: Claude Code session ID for resume
    """

    user_id: str
    pty: PTYSession
    workspace: Path
    output_buffer: OutputBuffer = field(default_factory=OutputBuffer)
    created_at: datetime = field(default_factory=datetime.now)
    claude_session_id: str | None = None
    _output_task: asyncio.Task | None = field(default=None, repr=False)
    _output_callback: Callable[[str, ParsedOutput], None] | None = field(
        default=None, repr=False
    )

    def is_alive(self) -> bool:
        """Check if the session is still active."""
        return self.pty.is_alive()

    def is_expired(self) -> bool:
        """Check if the session has timed out due to inactivity."""
        idle_seconds = (datetime.now() - self.pty.last_activity).total_seconds()
        return idle_seconds > SESSION_TIMEOUT

    async def send_input(self, text: str) -> None:
        """Send user input to Claude Code.

        Args:
            text: User input text
        """
        await self.pty.write_line(text)

    async def send_slash_command(self, command: str) -> None:
        """Send a slash command to Claude Code.

        Args:
            command: Slash command (e.g., "/clear", "/compact")
        """
        # Slash commands are just sent as regular input
        await self.pty.write_line(command)

    def get_statusbar(self) -> str | None:
        """Get current statusbar content."""
        return self.output_buffer.get_statusbar()


class SessionManager:
    """Manages all user Claude Code sessions."""

    def __init__(self):
        """Initialize session manager."""
        self._sessions: dict[str, UserSession] = {}
        self._output_callbacks: dict[str, Callable[[str, ParsedOutput], None]] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the session manager background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop the session manager and clean up all sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Terminate all sessions
        for user_id in list(self._sessions.keys()):
            await self.stop_session(user_id)

    async def _cleanup_loop(self) -> None:
        """Background task to clean up expired sessions."""
        while True:
            await asyncio.sleep(60)  # Check every minute

            expired = [
                user_id
                for user_id, session in self._sessions.items()
                if session.is_expired() or not session.is_alive()
            ]

            for user_id in expired:
                await self.stop_session(user_id)

    def set_output_callback(
        self,
        user_id: str,
        callback: Callable[[str, ParsedOutput], None],
    ) -> None:
        """Set callback for output from a user's session.

        Args:
            user_id: User ID
            callback: Function called with (user_id, parsed_output)
        """
        self._output_callbacks[user_id] = callback

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active session.

        Args:
            user_id: User ID

        Returns:
            True if session exists and is alive
        """
        session = self._sessions.get(user_id)
        return session is not None and session.is_alive()

    def get_session(self, user_id: str) -> UserSession | None:
        """Get a user's session if it exists.

        Args:
            user_id: User ID

        Returns:
            UserSession or None
        """
        session = self._sessions.get(user_id)
        if session and session.is_alive():
            return session
        return None

    async def start_session(
        self,
        user_id: str,
        resume_session_id: str | None = None,
        auth_method: AuthMethod | None = None,
    ) -> UserSession:
        """Start a new Claude Code session for a user.

        Args:
            user_id: User ID
            resume_session_id: Optional session ID to resume
            auth_method: Authentication method (auto-detected if None)

        Returns:
            The new or existing UserSession

        Raises:
            ValueError: If no valid authentication method available
        """
        # Stop existing session if any
        if user_id in self._sessions:
            await self.stop_session(user_id)

        # Determine auth method if not provided
        if auth_method is None:
            auth_method = get_auth_method(user_id)

        if auth_method == AuthMethod.NONE:
            raise ValueError(
                "No authentication configured. Use /cc login or set ANTHROPIC_API_KEY."
            )

        # Setup sandbox with auth method
        workspace, bwrap_cmd = setup_sandbox(user_id, auth_method)

        # Add resume flag if provided
        if resume_session_id:
            bwrap_cmd.extend(["--resume", resume_session_id])

        # Create PTY session
        pty_session = await create_pty_session(user_id, bwrap_cmd)

        # Create user session
        session = UserSession(
            user_id=user_id,
            pty=pty_session,
            workspace=workspace,
            claude_session_id=resume_session_id,
        )

        self._sessions[user_id] = session

        # Start output reading task
        session._output_task = asyncio.create_task(self._read_output_loop(user_id))

        return session

    async def stop_session(self, user_id: str) -> None:
        """Stop a user's session.

        Args:
            user_id: User ID
        """
        session = self._sessions.pop(user_id, None)
        if session:
            # Cancel output task
            if session._output_task:
                session._output_task.cancel()
                try:
                    await session._output_task
                except asyncio.CancelledError:
                    pass

            # Terminate PTY
            session.pty.terminate()

            # Cleanup sandbox (keep workspace)
            cleanup_sandbox(user_id, remove_workspace=False)

        # Remove callback
        self._output_callbacks.pop(user_id, None)

    async def send_input(self, user_id: str, text: str) -> bool:
        """Send input to a user's session.

        Args:
            user_id: User ID
            text: Input text

        Returns:
            True if sent successfully
        """
        session = self.get_session(user_id)
        if not session:
            return False

        await session.send_input(text)
        return True

    async def send_slash_command(self, user_id: str, command: str) -> bool:
        """Send a slash command to a user's session.

        Args:
            user_id: User ID
            command: Slash command

        Returns:
            True if sent successfully
        """
        session = self.get_session(user_id)
        if not session:
            return False

        await session.send_slash_command(command)
        return True

    async def _read_output_loop(self, user_id: str) -> None:
        """Background task to read and process output from a session.

        Args:
            user_id: User ID
        """
        session = self._sessions.get(user_id)
        if not session:
            return

        callback = self._output_callbacks.get(user_id)

        try:
            async for data in session.pty.read_stream(
                chunk_timeout=0.1,
                idle_timeout=300,  # 5 minutes idle timeout for reads
            ):
                # Parse output
                parsed = session.output_buffer.feed(data)

                # Call callback if set and there's content
                if callback and (parsed.content or parsed.statusbar):
                    try:
                        callback(user_id, parsed)
                    except Exception:
                        pass  # Don't let callback errors kill the loop

                # Small yield to prevent blocking
                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Session ended or error

    def get_all_sessions(self) -> dict[str, UserSession]:
        """Get all active sessions.

        Returns:
            Dict of user_id -> UserSession
        """
        return {
            user_id: session
            for user_id, session in self._sessions.items()
            if session.is_alive()
        }

    def get_session_info(self, user_id: str) -> dict | None:
        """Get info about a user's session.

        Args:
            user_id: User ID

        Returns:
            Dict with session info or None
        """
        session = self.get_session(user_id)
        if not session:
            return None

        uptime = (datetime.now() - session.created_at).total_seconds()
        idle = (datetime.now() - session.pty.last_activity).total_seconds()

        return {
            "user_id": user_id,
            "created_at": session.created_at.isoformat(),
            "uptime_seconds": uptime,
            "idle_seconds": idle,
            "workspace": str(session.workspace),
            "statusbar": session.get_statusbar(),
            "alive": session.is_alive(),
        }


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance.

    Returns:
        SessionManager singleton
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
