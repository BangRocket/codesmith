"""Session manager for stream-json based Claude Code sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .auth import AuthMethod, get_auth_method
from .config import SESSION_TIMEOUT
from .stream_handler import StreamSession, create_stream_session
from .stream_parser import StatusInfo, StreamOutputFormatter


@dataclass
class UserStreamSession:
    """A user's stream-based Claude Code session.

    Attributes:
        user_id: Discord user ID
        stream: StreamSession for Claude Code
        formatter: Output formatter for Discord
        created_at: When session was created
    """

    user_id: str
    stream: StreamSession
    formatter: StreamOutputFormatter = field(default_factory=StreamOutputFormatter)
    created_at: datetime = field(default_factory=datetime.now)
    _current_task: asyncio.Task | None = field(default=None, repr=False)

    def is_busy(self) -> bool:
        """Check if session is processing a request."""
        return self.stream.is_busy()

    def is_expired(self) -> bool:
        """Check if the session has timed out."""
        idle_seconds = (datetime.now() - self.stream.last_activity).total_seconds()
        return idle_seconds > SESSION_TIMEOUT

    @property
    def workspace(self) -> Path:
        """Get workspace path."""
        return self.stream.workspace

    @property
    def session_id(self) -> str | None:
        """Get Claude session ID."""
        return self.stream.session_id

    def get_status(self) -> StatusInfo:
        """Get current status info."""
        return self.formatter.get_status()


# Type for output callbacks
OutputCallback = Callable[[str, list[str], StatusInfo | None], Any]


class StreamSessionManager:
    """Manages all user stream-based Claude Code sessions."""

    def __init__(self):
        """Initialize session manager."""
        self._sessions: dict[str, UserStreamSession] = {}
        self._output_callbacks: dict[str, OutputCallback] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the session manager background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop the session manager and clean up."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Cancel all active requests
        for session in self._sessions.values():
            await session.stream.cancel()

        self._sessions.clear()
        self._output_callbacks.clear()

    async def _cleanup_loop(self) -> None:
        """Background task to clean up expired sessions."""
        while True:
            await asyncio.sleep(60)

            expired = [
                user_id
                for user_id, session in self._sessions.items()
                if session.is_expired()
            ]

            for user_id in expired:
                await self.stop_session(user_id)

    def set_output_callback(self, user_id: str, callback: OutputCallback) -> None:
        """Set callback for output from a user's session.

        Callback signature: (user_id, chunks, status) -> None
        - chunks: List of message strings to send
        - status: StatusInfo or None if not available yet
        """
        self._output_callbacks[user_id] = callback

    def has_session(self, user_id: str) -> bool:
        """Check if user has a session."""
        return user_id in self._sessions

    def get_session(self, user_id: str) -> UserStreamSession | None:
        """Get a user's session."""
        return self._sessions.get(user_id)

    async def start_session(
        self,
        user_id: str,
        auth_method: AuthMethod | None = None,
    ) -> UserStreamSession:
        """Start or get a stream session for a user.

        Unlike PTY sessions, stream sessions don't need to "start" a process -
        they just need to be created. The process runs per-message.

        Args:
            user_id: User ID
            auth_method: Authentication method (auto-detected if None)

        Returns:
            UserStreamSession

        Raises:
            ValueError: If no valid authentication available
        """
        # Return existing session if present
        if user_id in self._sessions:
            return self._sessions[user_id]

        # Determine auth method
        if auth_method is None:
            auth_method = get_auth_method(user_id)

        if auth_method == AuthMethod.NONE:
            raise ValueError(
                "No authentication configured. Use /cc login or set ANTHROPIC_API_KEY."
            )

        # Create stream session
        stream = await create_stream_session(user_id, auth_method)

        session = UserStreamSession(
            user_id=user_id,
            stream=stream,
        )

        self._sessions[user_id] = session
        return session

    async def stop_session(self, user_id: str) -> None:
        """Stop and remove a user's session."""
        session = self._sessions.pop(user_id, None)
        if session:
            await session.stream.cancel()

        self._output_callbacks.pop(user_id, None)

    async def send_message(
        self,
        user_id: str,
        message: str,
    ) -> bool:
        """Send a message to a user's session.

        This starts a Claude Code request and streams the response.
        Output is delivered via the registered callback.

        Args:
            user_id: User ID
            message: Message to send

        Returns:
            True if message was sent, False if no session
        """
        session = self.get_session(user_id)
        if not session:
            return False

        if session.is_busy():
            # Session is processing - queue this or reject?
            # For now, reject with a message via callback
            callback = self._output_callbacks.get(user_id)
            if callback:
                busy_msg = "*Session is busy. Please wait for the current response.*"
                callback(user_id, [busy_msg], None)
            return False

        # Start processing in background
        session._current_task = asyncio.create_task(
            self._process_message(user_id, message)
        )

        return True

    async def _process_message(self, user_id: str, message: str) -> None:
        """Process a message and deliver output via callback.

        Args:
            user_id: User ID
            message: Message to process
        """
        session = self.get_session(user_id)
        if not session:
            return

        callback = self._output_callbacks.get(user_id)
        formatter = session.formatter

        # Reset formatter for new response
        formatter.reset()

        try:
            async for event in session.stream.send_message(message):
                chunks = formatter.feed(event)

                if chunks and callback:
                    status = formatter.get_status() if event.type == "result" else None
                    try:
                        callback(user_id, chunks, status)
                    except Exception:
                        pass  # Don't let callback errors stop processing

            # Flush any remaining content
            remaining = formatter.flush()
            if remaining and callback:
                callback(user_id, remaining, formatter.get_status())

        except asyncio.CancelledError:
            raise
        except Exception as e:
            if callback:
                callback(user_id, [f"**Error:** {e}"], None)

    async def cancel_request(self, user_id: str) -> bool:
        """Cancel the current request for a user.

        Args:
            user_id: User ID

        Returns:
            True if cancelled, False if nothing to cancel
        """
        session = self.get_session(user_id)
        if not session or not session.is_busy():
            return False

        await session.stream.cancel()

        if session._current_task:
            session._current_task.cancel()
            session._current_task = None

        return True

    def get_session_info(self, user_id: str) -> dict[str, Any] | None:
        """Get info about a user's session."""
        session = self.get_session(user_id)
        if not session:
            return None

        stats = session.stream.get_stats()
        status = session.get_status()

        return {
            "user_id": user_id,
            "session_id": stats["session_id"],
            "model": stats["model"],
            "total_tokens": stats["total_input_tokens"] + stats["total_output_tokens"],
            "total_cost_usd": stats["total_cost_usd"],
            "is_busy": stats["is_busy"],
            "created_at": session.created_at.isoformat(),
            "workspace": str(session.workspace),
            "statusbar": status.format_statusbar(),
        }

    def get_all_sessions(self) -> dict[str, UserStreamSession]:
        """Get all active sessions."""
        return dict(self._sessions)


# Global session manager instance
_stream_session_manager: StreamSessionManager | None = None


def get_stream_session_manager() -> StreamSessionManager:
    """Get the global stream session manager instance."""
    global _stream_session_manager
    if _stream_session_manager is None:
        _stream_session_manager = StreamSessionManager()
    return _stream_session_manager
