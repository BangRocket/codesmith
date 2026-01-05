"""Stream-based session management for Claude Code.

Provides session wrapper that integrates ClaudeRunner with Discord.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .auth import AuthMethod
from .claude_runner import ClaudeMessage, ClaudeRunner
from .code_server import CodeServerInfo
from .config import ANTHROPIC_API_KEY, ensure_workspace


@dataclass
class StreamSession:
    """A user's Claude Code session using stream-json.

    Wraps ClaudeRunner with session metadata and stats tracking.
    """

    user_id: str
    runner: ClaudeRunner
    workspace: Path
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    is_busy: bool = False

    # Accumulated stats
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    # Code-server instance (if running)
    code_server: CodeServerInfo | None = None

    @property
    def session_id(self) -> str | None:
        """Get the Claude session ID."""
        return self.runner.session_id

    @property
    def model(self) -> str | None:
        """Get the current model name."""
        return self.runner.model

    def update_stats(self, message: ClaudeMessage) -> None:
        """Update session stats from a result message.

        Args:
            message: ClaudeMessage (should be type='result')
        """
        if message.is_result:
            usage = message.usage
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            self.total_cost_usd += message.cost_usd

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now()

    def get_stats(self) -> dict:
        """Get session statistics."""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "is_busy": self.is_busy,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "workspace": str(self.workspace),
        }

    def format_statusbar(self) -> str:
        """Format stats as a statusbar-like string."""
        parts = []

        if self.model:
            parts.append(self.model)

        total_tokens = self.total_input_tokens + self.total_output_tokens
        if total_tokens:
            parts.append(f"{total_tokens:,} tokens")

        if self.total_cost_usd > 0:
            parts.append(f"${self.total_cost_usd:.4f}")

        return " | ".join(parts) if parts else "No activity yet"


async def create_stream_session(
    user_id: str,
    auth_method: AuthMethod,
) -> StreamSession:
    """Create a new stream-based session.

    Args:
        user_id: Discord user ID
        auth_method: Authentication method to use

    Returns:
        New StreamSession instance
    """
    workspace = ensure_workspace(user_id)

    # Build extra environment based on auth method
    env: dict[str, str] = {}
    if auth_method == AuthMethod.API_KEY and ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

    # Create runner with workspace as HOME
    runner = ClaudeRunner(workspace=workspace, env=env)

    return StreamSession(
        user_id=user_id,
        runner=runner,
        workspace=workspace,
    )


class StreamSessionManager:
    """Manages stream-based Claude Code sessions for multiple users."""

    def __init__(self):
        """Initialize session manager."""
        self._sessions: dict[str, StreamSession] = {}

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active session."""
        return user_id in self._sessions

    def get_session(self, user_id: str) -> StreamSession | None:
        """Get a user's session."""
        return self._sessions.get(user_id)

    async def start_session(
        self,
        user_id: str,
        auth_method: AuthMethod,
    ) -> StreamSession:
        """Start or get a session for a user.

        Args:
            user_id: Discord user ID
            auth_method: Authentication method

        Returns:
            StreamSession instance
        """
        # Return existing session if present
        if user_id in self._sessions:
            return self._sessions[user_id]

        # Create new session
        session = await create_stream_session(user_id, auth_method)
        self._sessions[user_id] = session
        return session

    async def stop_session(self, user_id: str) -> None:
        """Stop and remove a user's session."""
        session = self._sessions.pop(user_id, None)
        if session:
            await session.runner.cancel()

    def get_session_info(self, user_id: str) -> dict | None:
        """Get info about a user's session."""
        session = self.get_session(user_id)
        if session:
            return session.get_stats()
        return None

    def get_all_sessions(self) -> dict[str, StreamSession]:
        """Get all active sessions."""
        return dict(self._sessions)


# Global session manager instance
_session_manager: StreamSessionManager | None = None


def get_stream_session_manager() -> StreamSessionManager:
    """Get the global stream session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = StreamSessionManager()
    return _session_manager
