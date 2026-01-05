"""Claude Code subprocess runner using stream-json output.

Based on patterns from happy-cli's SDK implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("codesmith.runner")


@dataclass
class ClaudeMessage:
    """Parsed message from Claude Code stream-json output.

    Message types:
    - system: Session init with session_id, model, tools
    - assistant: Claude's response with text/tool_use blocks
    - user: Tool results
    - result: Conversation end with stats
    - log: Debug logging
    """

    type: str
    content: dict[str, Any]

    @property
    def text(self) -> str | None:
        """Extract text from assistant messages."""
        if self.type == "assistant":
            message = self.content.get("message", {})
            content_blocks = message.get("content", [])
            texts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return "".join(texts) if texts else None
        return None

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        """Extract tool use blocks from assistant messages."""
        if self.type == "assistant":
            message = self.content.get("message", {})
            content_blocks = message.get("content", [])
            return [
                block
                for block in content_blocks
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
        return []

    @property
    def session_id(self) -> str | None:
        """Extract session_id from system or result messages."""
        return self.content.get("session_id")

    @property
    def model(self) -> str | None:
        """Extract model name from system messages."""
        return self.content.get("model")

    @property
    def is_result(self) -> bool:
        """Check if this is a result message (end of conversation)."""
        return self.type == "result"

    @property
    def usage(self) -> dict[str, int]:
        """Extract usage stats from result messages."""
        if self.is_result:
            return self.content.get("usage", {})
        return {}

    @property
    def cost_usd(self) -> float:
        """Extract cost from result messages."""
        if self.is_result:
            return self.content.get("total_cost_usd", 0.0)
        return 0.0

    @property
    def is_error(self) -> bool:
        """Check if result indicates an error."""
        if self.is_result:
            return self.content.get("is_error", False)
        return False


class ClaudeRunner:
    """Runs Claude Code with stream-json output.

    Each call to run() spawns a new Claude Code process for a single prompt.
    Session continuity is maintained via --resume with the session_id.
    """

    def __init__(
        self,
        workspace: Path,
        env: dict[str, str] | None = None,
    ):
        """Initialize the runner.

        Args:
            workspace: Working directory for Claude Code (also used as HOME)
            env: Additional environment variables
        """
        self.workspace = workspace
        self.extra_env = env or {}
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.model: str | None = None

    def _build_args(self, prompt: str) -> list[str]:
        """Build command arguments for Claude Code."""
        args = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

        if self.session_id:
            args.extend(["--resume", self.session_id])

        return args

    def _build_env(self) -> dict[str, str]:
        """Build environment for the subprocess."""
        env = os.environ.copy()

        # Set HOME to workspace so Claude finds .claude/.credentials.json
        env["HOME"] = str(self.workspace)

        # Add any extra environment variables (e.g., ANTHROPIC_API_KEY)
        env.update(self.extra_env)

        return env

    async def run(
        self,
        prompt: str,
        on_message: Callable[[ClaudeMessage], None] | None = None,
    ) -> AsyncIterator[ClaudeMessage]:
        """Run a prompt and stream messages.

        Args:
            prompt: The user's prompt/message
            on_message: Optional callback for each message

        Yields:
            ClaudeMessage objects as they arrive
        """
        args = self._build_args(prompt)
        env = self._build_env()

        # Ensure workspace exists
        self.workspace.mkdir(parents=True, exist_ok=True)

        logger.info("Starting Claude Code process")
        logger.debug(f"Command: {' '.join(args)}")
        logger.debug(f"Working directory: {self.workspace}")
        logger.debug(f"HOME={env.get('HOME')}")

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace),
            env=env,
        )

        logger.info(f"Process started with PID {self.process.pid}")

        # Close stdin since we're using -p mode (prompt passed as argument)
        if self.process.stdin:
            self.process.stdin.close()

        # Start stderr reader task
        stderr_task = asyncio.create_task(self._read_stderr())

        try:
            line_count = 0
            async for line in self.process.stdout:
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                line_count += 1
                truncated = line_str[:200] + "..." if len(line_str) > 200 else line_str
                logger.debug(f"[stdout:{line_count}] {truncated}")

                try:
                    data = json.loads(line_str)
                    msg = ClaudeMessage(
                        type=data.get("type", "unknown"),
                        content=data,
                    )

                    logger.info(f"Received message type: {msg.type}")

                    # Track session ID and model from system/result messages
                    if msg.session_id:
                        logger.info(f"Session ID: {msg.session_id}")
                        self.session_id = msg.session_id
                    if msg.model:
                        logger.info(f"Model: {msg.model}")
                        self.model = msg.model

                    # Log specific message details
                    if msg.type == "assistant" and msg.text:
                        txt = msg.text
                        preview = txt[:100] + "..." if len(txt) > 100 else txt
                        logger.debug(f"Assistant text: {preview}")
                    if msg.type == "result":
                        logger.info(f"Result: err={msg.is_error}, ${msg.cost_usd:.4f}")

                    if on_message:
                        try:
                            on_message(msg)
                        except Exception as e:
                            logger.exception(f"Callback error: {e}")

                    yield msg

                except json.JSONDecodeError as e:
                    logger.warning(f"Non-JSON line: {line_str[:100]} (error: {e})")
                    continue

            logger.info(f"Finished reading stdout ({line_count} lines)")

        finally:
            # Wait for process to complete
            if self.process:
                return_code = await self.process.wait()
                logger.info(f"Process exited with code {return_code}")

            # Wait for stderr task
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    async def _read_stderr(self) -> None:
        """Read and log stderr output."""
        if not self.process or not self.process.stderr:
            return

        try:
            async for line in self.process.stderr:
                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str:
                    logger.warning(f"[stderr] {line_str}")
        except asyncio.CancelledError:
            pass

    async def cancel(self) -> None:
        """Cancel the current run."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()

    @property
    def is_running(self) -> bool:
        """Check if a process is currently running."""
        return self.process is not None and self.process.returncode is None
