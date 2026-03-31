"""High-level agent spawning using the adapter interface."""

import logging
from typing import Any

from reknew.adapters.base_adapter import AgentAdapter, AgentStatus
from reknew.adapters.claude_adapter import ClaudeCodeAdapter
from reknew.adapters.codex_adapter import CodexAdapter
from reknew.adapters.gemini_adapter import GeminiAdapter
from reknew.config import ReknewConfig

logger = logging.getLogger(__name__)

ADAPTER_MAP: dict[str, type[AgentAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
}


class AgentSpawner:
    """Spawns agents using the configured adapter.

    Integrates with Delegate's worktree manager for isolation.
    Tracks all running agents and their handles.
    """

    def __init__(self, config: ReknewConfig):
        """Initialize with the ReKnew configuration.

        Args:
            config: validated ReknewConfig instance
        """
        self.config = config
        self.adapter = self._load_adapter(config.defaults.agent)
        self.running: dict[str, dict] = {}

    def _load_adapter(self, agent_name: str) -> AgentAdapter:
        """Load the adapter for the configured agent.

        Args:
            agent_name: one of the supported agent names

        Returns:
            Instantiated AgentAdapter

        Raises:
            ValueError: for unknown agent names
        """
        adapter_cls = ADAPTER_MAP.get(agent_name)
        if adapter_cls is None:
            raise ValueError(
                f"Unknown agent '{agent_name}'. "
                f"Supported: {sorted(ADAPTER_MAP.keys())}"
            )
        if agent_name == "claude-code":
            return adapter_cls(
                stuck_timeout=self.config.defaults.stuck_timeout
            )
        return adapter_cls()

    def spawn_task(
        self, task_id: str, worktree_path: str, prompt: str
    ) -> None:
        """Spawn an agent for a task.

        Args:
            task_id: unique task identifier
            worktree_path: absolute path to the git worktree
            prompt: the full task prompt
        """
        handle = self.adapter.spawn(worktree_path, prompt, task_id)
        self.running[task_id] = {
            "handle": handle,
            "worktree": worktree_path,
            "adapter": self.adapter,
        }
        logger.info("Spawned agent for %s in %s", task_id, worktree_path)

    def spawn_wave(self, tasks: list[dict], worktree_manager: Any) -> None:
        """Spawn agents for all tasks in a wave (parallel).

        For each task:
        1. Create a worktree via worktree_manager
        2. Spawn the agent in the worktree

        Args:
            tasks: list of task dicts from the task graph
            worktree_manager: object with create_worktree(task_id) -> path method
        """
        for task in tasks:
            task_id = task["id"]
            worktree_path = worktree_manager.create_worktree(task_id)
            prompt = task.get("description", "")
            self.spawn_task(task_id, str(worktree_path), prompt)

    def get_status(self, task_id: str) -> AgentStatus:
        """Get status of a running agent.

        Args:
            task_id: the task identifier

        Returns:
            AgentStatus enum value
        """
        info = self.running.get(task_id)
        if info is None:
            return AgentStatus.CRASHED
        adapter: AgentAdapter = info["adapter"]
        return adapter.poll_status(info["handle"])

    def get_all_statuses(self) -> dict[str, AgentStatus]:
        """Get status of all running agents.

        Returns:
            Dict mapping task_id to AgentStatus
        """
        return {tid: self.get_status(tid) for tid in self.running}
