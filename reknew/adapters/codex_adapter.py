"""Adapter for OpenAI Codex API (stub)."""

from typing import Any

from .base_adapter import AgentAdapter, AgentStatus


class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex API.

    Unlike Claude Code (subprocess), Codex is API-based.
    spawn() makes an API call. poll_status() checks the API.
    """

    # TODO: Implement when testing Codex

    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> Any:
        raise NotImplementedError("Codex adapter not yet implemented")

    def poll_status(self, handle: Any) -> AgentStatus:
        raise NotImplementedError("Codex adapter not yet implemented")

    def send_feedback(self, handle: Any, feedback: str) -> Any:
        raise NotImplementedError("Codex adapter not yet implemented")
