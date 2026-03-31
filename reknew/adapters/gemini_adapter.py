"""Adapter for Gemini CLI (stub)."""

from typing import Any

from .base_adapter import AgentAdapter, AgentStatus


class GeminiAdapter(AgentAdapter):
    """Adapter for Gemini CLI.

    Similar to Claude Code -- subprocess-based.
    """

    # TODO: Implement when testing Gemini

    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> Any:
        raise NotImplementedError("Gemini adapter not yet implemented")

    def poll_status(self, handle: Any) -> AgentStatus:
        raise NotImplementedError("Gemini adapter not yet implemented")

    def send_feedback(self, handle: Any, feedback: str) -> Any:
        raise NotImplementedError("Gemini adapter not yet implemented")
