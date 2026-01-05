"""Code-server management for browser-based file access.

Spawns code-server instances on-demand for each user session,
providing VS Code in the browser to view/edit workspace files.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import CODE_SERVER_DOMAIN, CODE_SERVER_ENABLED, CODE_SERVER_PORT_BASE

logger = logging.getLogger("codesmith.code_server")


def is_code_server_available() -> bool:
    """Check if code-server is installed and enabled."""
    if not CODE_SERVER_ENABLED:
        return False
    return shutil.which("code-server") is not None


@dataclass
class CodeServerInfo:
    """Information about a running code-server instance."""

    port: int
    password: str
    url: str
    process: asyncio.subprocess.Process


class CodeServerManager:
    """Manages code-server instances for user sessions."""

    def __init__(self):
        """Initialize the manager."""
        self._instances: dict[str, CodeServerInfo] = {}
        self._used_ports: set[int] = set()

    def _allocate_port(self, user_id: str) -> int:
        """Allocate a port for a user based on their ID.

        Uses hash of user_id to get consistent port assignment,
        with collision handling if port is in use.
        """
        base_port = CODE_SERVER_PORT_BASE + (hash(user_id) % 1000)

        port = base_port
        while port in self._used_ports:
            port += 1
            if port >= CODE_SERVER_PORT_BASE + 1000:
                port = CODE_SERVER_PORT_BASE

            if port == base_port:
                raise RuntimeError("No available ports for code-server")

        self._used_ports.add(port)
        return port

    def _free_port(self, port: int) -> None:
        """Free a port for reuse."""
        self._used_ports.discard(port)

    async def start(self, user_id: str, workspace: Path) -> CodeServerInfo | None:
        """Start a code-server instance for a user.

        Args:
            user_id: Discord user ID
            workspace: Path to user's workspace directory

        Returns:
            CodeServerInfo with connection details, or None if not available
        """
        # Check if code-server is available
        if not is_code_server_available():
            logger.info("code-server not available, skipping")
            return None

        # Check if already running
        if user_id in self._instances:
            return self._instances[user_id]

        port = self._allocate_port(user_id)
        password = secrets.token_urlsafe(16)

        logger.info(f"Starting code-server for {user_id} on port {port}")
        logger.debug(f"Workspace: {workspace}")

        process = await asyncio.create_subprocess_exec(
            "code-server",
            "--bind-addr",
            f"127.0.0.1:{port}",
            "--auth",
            "password",
            "--password",
            password,
            "--disable-telemetry",
            "--disable-update-check",
            str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        url = f"https://{port}.{CODE_SERVER_DOMAIN}"

        info = CodeServerInfo(
            port=port,
            password=password,
            url=url,
            process=process,
        )

        self._instances[user_id] = info

        # Start background task to log stderr
        asyncio.create_task(self._log_stderr(user_id, process))

        logger.info(f"code-server started for {user_id}: {url}")
        return info

    async def _log_stderr(
        self,
        user_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Log stderr output from code-server."""
        if not process.stderr:
            return

        try:
            async for line in process.stderr:
                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str:
                    logger.debug(f"[code-server:{user_id}] {line_str}")
        except asyncio.CancelledError:
            pass

    async def stop(self, user_id: str) -> bool:
        """Stop a user's code-server instance.

        Args:
            user_id: Discord user ID

        Returns:
            True if instance was stopped, False if not found
        """
        info = self._instances.pop(user_id, None)
        if not info:
            return False

        logger.info(f"Stopping code-server for {user_id} on port {info.port}")

        if info.process.returncode is None:
            info.process.terminate()
            try:
                await asyncio.wait_for(info.process.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning(f"code-server for {user_id} didn't terminate, killing")
                info.process.kill()
                await info.process.wait()

        self._free_port(info.port)
        logger.info(f"code-server stopped for {user_id}")
        return True

    def get(self, user_id: str) -> CodeServerInfo | None:
        """Get code-server info for a user."""
        return self._instances.get(user_id)

    def has_instance(self, user_id: str) -> bool:
        """Check if user has a running code-server."""
        return user_id in self._instances

    async def stop_all(self) -> None:
        """Stop all running code-server instances."""
        for user_id in list(self._instances.keys()):
            await self.stop(user_id)


# Global instance
_manager: CodeServerManager | None = None


def get_code_server_manager() -> CodeServerManager:
    """Get the global CodeServerManager instance."""
    global _manager
    if _manager is None:
        _manager = CodeServerManager()
    return _manager
