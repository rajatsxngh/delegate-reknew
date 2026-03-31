"""Async polling loop that watches all running agents."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from reknew.adapters.base_adapter import AgentAdapter, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class MonitoredAgent:
    """State for a single monitored agent."""

    task_id: str
    handle: Any
    adapter: AgentAdapter
    worktree_path: str
    max_runtime: int
    start_time: float = field(default_factory=time.time)
    last_status: AgentStatus = AgentStatus.RUNNING


TERMINAL_STATUSES = {
    AgentStatus.DONE,
    AgentStatus.STUCK,
    AgentStatus.CRASHED,
    AgentStatus.TIMEOUT,
}


class AgentMonitor:
    """Monitors all running agents via asyncio polling.

    Polls adapters for status and fires callbacks when agents
    complete, get stuck, or crash.
    """

    def __init__(self, poll_interval: int = 10):
        """Initialize the monitor.

        Args:
            poll_interval: seconds between status checks
        """
        self.poll_interval = poll_interval
        self._agents: dict[str, MonitoredAgent] = {}
        self._callbacks: dict[str, list[Callable]] = {}

    def register(
        self,
        task_id: str,
        handle: Any,
        adapter: AgentAdapter,
        worktree_path: str,
        max_runtime: int = 3600,
    ) -> None:
        """Register an agent for monitoring.

        Args:
            task_id: unique task identifier
            handle: opaque handle from adapter.spawn()
            adapter: the AgentAdapter that spawned this agent
            worktree_path: path to the agent's worktree
            max_runtime: max seconds before TIMEOUT
        """
        self._agents[task_id] = MonitoredAgent(
            task_id=task_id,
            handle=handle,
            adapter=adapter,
            worktree_path=worktree_path,
            max_runtime=max_runtime,
        )
        logger.info("Monitoring agent %s (max %ds)", task_id, max_runtime)

    def on(self, event: str, callback: Callable) -> None:
        """Register callback for events: 'done', 'stuck', 'crashed', 'timeout'.

        Args:
            event: event name
            callback: callable(task_id, agent) to call when event fires
        """
        self._callbacks.setdefault(event, []).append(callback)

    def _fire(self, event: str, task_id: str, agent: MonitoredAgent) -> None:
        """Fire all callbacks for an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(task_id, agent)
            except Exception:
                logger.exception("Callback error for %s on %s", event, task_id)

    async def run(self) -> None:
        """Main monitoring loop. Runs until all agents finish.

        Every poll_interval seconds:
        1. For each registered agent:
           a. Check if exceeded max_runtime -> TIMEOUT
           b. Call adapter.poll_status(handle)
           c. If status changed -> fire callback
           d. If terminal -> remove from monitoring
        2. If no agents left -> return
        """
        while self._agents:
            finished: list[str] = []

            for task_id, agent in self._agents.items():
                elapsed = time.time() - agent.start_time

                # Check timeout
                if elapsed > agent.max_runtime:
                    new_status = AgentStatus.TIMEOUT
                else:
                    new_status = agent.adapter.poll_status(agent.handle)

                if new_status != agent.last_status:
                    agent.last_status = new_status
                    event_name = new_status.value
                    logger.info(
                        "Agent %s status: %s (%.0fs elapsed)",
                        task_id, event_name, elapsed,
                    )
                    self._fire(event_name, task_id, agent)

                if new_status in TERMINAL_STATUSES:
                    finished.append(task_id)

            for task_id in finished:
                del self._agents[task_id]

            if self._agents:
                await asyncio.sleep(self.poll_interval)

    def get_summary(self) -> dict[str, dict]:
        """Get current status of all monitored agents.

        Returns:
            Dict of {task_id: {status, elapsed_seconds, worktree_path}}
        """
        summary: dict[str, dict] = {}
        now = time.time()
        for task_id, agent in self._agents.items():
            summary[task_id] = {
                "status": agent.last_status.value,
                "elapsed_seconds": round(now - agent.start_time, 1),
                "worktree_path": agent.worktree_path,
            }
        return summary
