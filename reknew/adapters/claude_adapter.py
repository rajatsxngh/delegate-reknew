"""Adapter for Claude Code CLI."""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .base_adapter import AgentAdapter, AgentStatus

logger = logging.getLogger(__name__)

LOGS_DIR = Path.home() / ".reknew" / "logs"


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code CLI.

    Spawns Claude Code as a subprocess with --print flag (non-interactive).
    Monitors by checking process status and log file growth.
    Sends feedback by killing and re-spawning with new prompt.
    """

    def __init__(self, stuck_timeout: int = 300):
        """Initialize the adapter.

        Args:
            stuck_timeout: seconds with no log output = stuck (default 300 = 5 min)
        """
        self.stuck_timeout = stuck_timeout
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, str] = {}
        self._last_activity: dict[str, float] = {}
        self._last_log_size: dict[str, int] = {}
        self._worktree_paths: dict[str, str] = {}
        self._prompts: dict[str, str] = {}

    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> str:
        """Spawn Claude Code in the worktree.

        Uses: claude --print --dangerously-skip-permissions "{prompt}"
        Log output goes to: ~/.reknew/logs/{task_id}.log

        Args:
            worktree_path: absolute path to the git worktree
            prompt: the full task prompt
            task_id: unique task identifier (e.g., "T0114")

        Returns:
            task_id as the handle

        Raises:
            FileNotFoundError: if worktree_path doesn't exist
            RuntimeError: if claude CLI not found
        """
        wt = Path(worktree_path)
        if not wt.exists():
            raise FileNotFoundError(f"Worktree not found: {worktree_path}")

        self._ensure_claude_installed()

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{task_id}.log"

        log_file = open(log_path, "w")
        process = subprocess.Popen(
            ["claude", "--print", "--dangerously-skip-permissions", prompt],
            cwd=worktree_path,
            stdout=log_file,
            stderr=log_file,
        )

        self._processes[task_id] = process
        self._log_files[task_id] = str(log_path)
        self._last_activity[task_id] = time.time()
        self._last_log_size[task_id] = 0
        self._worktree_paths[task_id] = worktree_path
        self._prompts[task_id] = prompt

        logger.info("Spawned Claude Code for %s in %s (pid=%d)",
                     task_id, worktree_path, process.pid)
        return task_id

    def poll_status(self, handle: str) -> AgentStatus:
        """Check Claude Code process status.

        Args:
            handle: task_id returned by spawn()

        Returns:
            AgentStatus enum value
        """
        task_id = handle
        process = self._processes.get(task_id)
        if process is None:
            return AgentStatus.CRASHED

        rc = process.poll()
        if rc is not None:
            return AgentStatus.DONE if rc == 0 else AgentStatus.CRASHED

        # Check log file growth for stuck detection
        log_path = self._log_files.get(task_id, "")
        try:
            current_size = os.path.getsize(log_path)
        except OSError:
            current_size = 0

        last_size = self._last_log_size.get(task_id, 0)
        if current_size > last_size:
            self._last_activity[task_id] = time.time()
            self._last_log_size[task_id] = current_size
            return AgentStatus.RUNNING

        elapsed = time.time() - self._last_activity.get(task_id, time.time())
        if elapsed > self.stuck_timeout:
            return AgentStatus.STUCK

        return AgentStatus.RUNNING

    def send_feedback(self, handle: str, feedback: str) -> str:
        """Kill current process, re-spawn with feedback as prompt.

        The feedback string contains CI error logs or review comments.
        The new prompt includes the original context plus the feedback.

        Args:
            handle: task_id returned by spawn()
            feedback: formatted string with error logs or review comments

        Returns:
            New handle (same task_id, new process)
        """
        task_id = handle
        process = self._processes.get(task_id)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

        original_prompt = self._prompts.get(task_id, "")
        worktree_path = self._worktree_paths.get(task_id, "")

        new_prompt = (
            f"{original_prompt}\n\n"
            f"--- FEEDBACK ---\n"
            f"The previous attempt had issues. Please address the following:\n\n"
            f"{feedback}"
        )

        return self.spawn(worktree_path, new_prompt, task_id)

    def _ensure_claude_installed(self) -> None:
        """Check that 'claude' CLI is available.

        Raises:
            RuntimeError: with install instructions if not found.
        """
        if not shutil.which("claude"):
            raise RuntimeError(
                "Claude Code CLI not found. "
                "Install: npm install -g @anthropic-ai/claude-code"
            )
