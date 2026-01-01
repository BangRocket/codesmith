"""PTY session handler for Claude Code processes."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import struct
import subprocess
import termios
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from .config import PTY_COLS, PTY_ROWS


@dataclass
class PTYSession:
    """Manages a PTY session for Claude Code.

    Attributes:
        user_id: User identifier for this session
        master_fd: Master file descriptor for PTY
        slave_fd: Slave file descriptor for PTY
        process: The subprocess running in the PTY
        created_at: When the session was created
        last_activity: Last time input/output occurred
    """

    user_id: str
    master_fd: int = -1
    slave_fd: int = -1
    process: subprocess.Popen | None = None
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    _read_buffer: bytes = field(default=b"", repr=False)

    def is_alive(self) -> bool:
        """Check if the PTY process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def start(self, command: list[str]) -> None:
        """Start a new PTY session with the given command.

        Args:
            command: Command and arguments to run
        """
        # Create PTY pair
        self.master_fd, self.slave_fd = pty.openpty()

        # Set terminal size
        self._set_winsize(PTY_ROWS, PTY_COLS)

        # Make master non-blocking for async reads
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Start process with PTY as stdin/stdout/stderr
        self.process = subprocess.Popen(
            command,
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            preexec_fn=os.setsid,  # Create new session
            close_fds=True,
        )

        # Close slave fd in parent - child has it
        os.close(self.slave_fd)
        self.slave_fd = -1

        self.last_activity = datetime.now()

    def _set_winsize(self, rows: int, cols: int) -> None:
        """Set the terminal window size."""
        if self.master_fd >= 0:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        self._set_winsize(rows, cols)

    async def write(self, data: str) -> None:
        """Write data to the PTY stdin.

        Args:
            data: String data to send
        """
        if self.master_fd < 0 or not self.is_alive():
            raise RuntimeError("PTY session not active")

        encoded = data.encode("utf-8")
        loop = asyncio.get_event_loop()

        await loop.run_in_executor(None, os.write, self.master_fd, encoded)
        self.last_activity = datetime.now()

    async def write_line(self, line: str) -> None:
        """Write a line to the PTY (adds newline).

        Args:
            line: Line to send (newline added automatically)
        """
        await self.write(line + "\n")

    async def read(self, timeout: float = 0.1) -> bytes:
        """Read available data from PTY.

        Args:
            timeout: How long to wait for data (seconds)

        Returns:
            Bytes read from PTY (may be empty)
        """
        if self.master_fd < 0:
            return b""

        loop = asyncio.get_event_loop()

        try:
            # Use select to check if data is available
            import select

            readable, _, _ = await loop.run_in_executor(
                None, lambda: select.select([self.master_fd], [], [], timeout)
            )

            if self.master_fd in readable:
                data = await loop.run_in_executor(
                    None, lambda: os.read(self.master_fd, 4096)
                )
                if data:
                    self.last_activity = datetime.now()
                return data

        except OSError:
            # PTY closed or error
            pass

        return b""

    async def read_stream(
        self,
        chunk_timeout: float = 0.1,
        idle_timeout: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """Stream data from PTY as it becomes available.

        Args:
            chunk_timeout: Timeout for each read attempt
            idle_timeout: Stop yielding after this much idle time

        Yields:
            Bytes chunks as they arrive
        """
        idle_start = datetime.now()

        while self.is_alive():
            data = await self.read(timeout=chunk_timeout)

            if data:
                idle_start = datetime.now()
                yield data
            else:
                # Check idle timeout
                idle_seconds = (datetime.now() - idle_start).total_seconds()
                if idle_seconds > idle_timeout:
                    break

            # Small delay to prevent tight loop
            await asyncio.sleep(0.01)

    def send_signal(self, sig: int) -> None:
        """Send a signal to the PTY process.

        Args:
            sig: Signal number (e.g., signal.SIGINT)
        """
        if self.process and self.is_alive():
            self.process.send_signal(sig)

    def send_interrupt(self) -> None:
        """Send Ctrl+C to the PTY."""
        if self.master_fd >= 0:
            os.write(self.master_fd, b"\x03")  # Ctrl+C

    def send_eof(self) -> None:
        """Send Ctrl+D (EOF) to the PTY."""
        if self.master_fd >= 0:
            os.write(self.master_fd, b"\x04")  # Ctrl+D

    def terminate(self) -> None:
        """Terminate the PTY session."""
        if self.process and self.is_alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1

    def __del__(self):
        """Cleanup on deletion."""
        self.terminate()


async def create_pty_session(user_id: str, command: list[str]) -> PTYSession:
    """Create and start a new PTY session.

    Args:
        user_id: User identifier
        command: Command to run in the PTY

    Returns:
        Started PTYSession
    """
    session = PTYSession(user_id=user_id)
    session.start(command)
    return session
