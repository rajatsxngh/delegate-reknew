"""Abstract interface that all LLM adapters implement."""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentStatus(Enum):
    """Status of a running agent."""

    RUNNING = "running"
    DONE = "done"
    STUCK = "stuck"
    CRASHED = "crashed"
    TIMEOUT = "timeout"


@dataclass
class FileChange:
    """A single file changed by an agent."""

    path: str
    change_type: str  # "added", "modified", "deleted"
    diff: str


class AgentAdapter(ABC):
    """Standard interface for all coding agents.

    The orchestration core calls these 4 methods without knowing
    which LLM is behind the adapter. Adding a new LLM = implementing
    these 4 methods in a new file.

    The handle returned by spawn() is opaque -- it can be a PID,
    a session ID, an API response object, whatever the adapter needs.
    """

    @abstractmethod
    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> Any:
        """Start the agent in the given worktree.

        Args:
            worktree_path: absolute path to the git worktree
            prompt: the full task prompt (may be very long)
            task_id: unique task identifier (e.g., "T0114")

        Returns:
            Opaque handle used by poll_status and send_feedback

        Raises:
            FileNotFoundError: if worktree_path doesn't exist
            RuntimeError: if agent binary not found
        """

    @abstractmethod
    def poll_status(self, handle: Any) -> AgentStatus:
        """Check if the agent is done, stuck, or crashed.

        Called repeatedly by the monitor's asyncio polling loop
        (every poll_interval seconds, default 10).

        Returns:
            AgentStatus enum value
        """

    @abstractmethod
    def send_feedback(self, handle: Any, feedback: str) -> Any:
        """Send CI errors or review comments back to the agent.

        For subprocess-based agents: kills the current process and
        re-spawns with the feedback as the new prompt.

        For API-based agents: sends a follow-up message to the session.

        Args:
            handle: the handle from spawn()
            feedback: formatted string with error logs or review comments

        Returns:
            New handle (may be different if process was restarted)
        """

    def get_changes(self, worktree_path: str) -> list[FileChange]:
        """Collect changed files after the agent completes.

        Default implementation uses git diff. Most adapters can
        use this default since all agents commit to git.

        Args:
            worktree_path: absolute path to the git worktree

        Returns:
            List of FileChange objects with path, type, and diff
        """
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD~1"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "diff", "--name-status", "main"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

        changes: list[FileChange] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status_code = parts[0].strip()
            file_path = parts[1].strip()

            change_type = {
                "A": "added",
                "M": "modified",
                "D": "deleted",
                "R": "renamed",
                "C": "copied",
            }.get(status_code[0], "modified")

            diff_result = subprocess.run(
                ["git", "diff", "HEAD~1", "--", file_path],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            changes.append(FileChange(
                path=file_path,
                change_type=change_type,
                diff=diff_result.stdout,
            ))

        return changes
