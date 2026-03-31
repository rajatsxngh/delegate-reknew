"""Central daemon that ties the entire ReKnew pipeline together."""

import asyncio
import logging

import uvicorn

from reknew.agents.monitor import AgentMonitor
from reknew.agents.spawner import AgentSpawner
from reknew.config import ReknewConfig, load_config
from reknew.connectors.github_connector import GitHubConnector
from reknew.dashboard.api import create_app
from reknew.dashboard.websocket import EventBroadcaster
from reknew.github.api import GitHubAPI
from reknew.github.pr_manager import PRManager
from reknew.github.webhook_listener import WebhookListener
from reknew.orchestration.capacity_router import CapacityRouter
from reknew.orchestration.task_breakdown import break_down_spec
from reknew.orchestration.task_state import Task, TaskState
from reknew.reactions.engine import ReactionsEngine

logger = logging.getLogger(__name__)


class ReknewDaemon:
    """Central orchestration daemon.

    Wraps Delegate's existing daemon and adds the ReKnew layer:
    - Issue ingestion (via connector)
    - Capacity routing (via router)
    - Reactions handling (via engine)
    - Dashboard serving (via FastAPI)

    Runs as an asyncio event loop with multiple concurrent tasks:
    1. GitHub webhook/poll listener
    2. Reactions engine event handler
    3. FastAPI server for dashboard
    """

    def __init__(self, config_path: str = "reknew.yaml"):
        """Initialize all ReKnew components.

        Args:
            config_path: path to reknew.yaml
        """
        self.config = load_config(config_path)

        # ReKnew components
        self.github_api = GitHubAPI()
        self.connector = GitHubConnector()
        self.pr_manager = PRManager(self.github_api, self.config)
        self.router = CapacityRouter(self.config.capacity.rules)
        self.spawner = AgentSpawner(self.config)
        self.monitor = AgentMonitor(
            poll_interval=self.config.defaults.stuck_timeout
        )
        self.reactions = ReactionsEngine(
            self.config, self.pr_manager, self.spawner
        )
        self.webhook = WebhookListener(self.config, self.pr_manager)
        self.broadcaster = EventBroadcaster()

        # Task storage
        self.tasks: dict[str, Task] = {}

        # Wire up event callbacks
        self._setup_callbacks()

    def _setup_callbacks(self) -> None:
        """Connect all event handlers.

        Monitor -> agent done/stuck/crashed -> create PR or handle failure
        Webhook -> ci-completed/review-submitted -> reactions engine
        """
        def on_agent_done(task_id: str, agent: object) -> None:
            task = self.tasks.get(task_id)
            if task:
                try:
                    task.transition(TaskState.IN_REVIEW)
                except ValueError:
                    pass
            asyncio.ensure_future(
                self.broadcaster.emit(
                    "agent_status",
                    {"task_id": task_id, "status": "done"},
                )
            )

        def on_agent_failed(task_id: str, agent: object) -> None:
            task = self.tasks.get(task_id)
            if task:
                try:
                    task.transition(TaskState.FAILED)
                except ValueError:
                    pass
            asyncio.ensure_future(
                self.broadcaster.emit(
                    "agent_status",
                    {"task_id": task_id, "status": "failed"},
                )
            )

        self.monitor.on("done", on_agent_done)
        self.monitor.on("crashed", on_agent_failed)
        self.monitor.on("stuck", on_agent_failed)
        self.monitor.on("timeout", on_agent_failed)

        def on_ci_completed(
            task_id: str, pr_number: int, data: object
        ) -> None:
            asyncio.ensure_future(
                self.reactions.handle_event(
                    "ci-completed", task_id, pr_number,
                    self._get_repo_for_task(task_id),
                )
            )

        def on_review(
            task_id: str, pr_number: int, data: object
        ) -> None:
            asyncio.ensure_future(
                self.reactions.handle_event(
                    "review-submitted", task_id, pr_number,
                    self._get_repo_for_task(task_id),
                )
            )

        self.webhook.on("ci-completed", on_ci_completed)
        self.webhook.on("review-submitted", on_review)

    def _get_repo_for_task(self, task_id: str) -> str:
        """Look up the repo name for a task (first project for now)."""
        for proj in self.config.projects.values():
            return proj.repo
        return ""

    async def process_issue(
        self, project_name: str, issue_number: int
    ) -> list[str]:
        """Process an issue through the full pipeline.

        Args:
            project_name: key in config.projects
            issue_number: GitHub issue number

        Returns:
            list of task IDs created
        """
        project = self.config.projects[project_name]
        repo_name = project.repo

        # Fetch issue
        ctx = self.connector.fetch_issue(repo_name, issue_number)
        openspec_text = self.connector.format_for_openspec(ctx)

        # Break into tasks
        raw_tasks = break_down_spec(openspec_text)

        # Route through capacity
        labels = ctx.labels
        decisions = self.router.route_batch(raw_tasks, labels)

        # Create Task objects
        task_ids: list[str] = []
        for raw in raw_tasks:
            tid = raw["id"]
            task = Task(
                id=tid,
                title=raw["title"],
                description=raw["description"],
                files=raw["files"],
                dependencies=raw["dependencies"],
                complexity=raw["complexity"],
                estimated_minutes=raw["estimated_minutes"],
            )
            decision = decisions[tid]
            task.routing_decision = decision
            task.assigned_to = decision.target
            task.transition(TaskState.CAPACITY_ROUTED)
            self.tasks[tid] = task
            task_ids.append(tid)

            await self.broadcaster.emit("task_update", {
                "task_id": tid,
                "old_state": "todo",
                "new_state": "capacity_routed",
            })

        await self.broadcaster.emit("capacity_update", {
            "human": sum(
                1 for d in decisions.values() if d.target == "human"
            ),
            "ai": sum(
                1 for d in decisions.values() if d.target == "ai"
            ),
            "total": len(decisions),
        })

        return task_ids

    async def run(self) -> None:
        """Start the daemon.

        Launches concurrent tasks:
        1. Webhook/poll listener
        2. FastAPI server (dashboard)

        Runs until interrupted.
        """
        app = create_app(self.config, daemon=self)
        app.state.broadcaster = self.broadcaster

        server_config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.config.port,
            log_level="info",
        )
        server = uvicorn.Server(server_config)

        logger.info(
            "ReKnew daemon starting on port %d", self.config.port
        )

        # Run server and poll loop concurrently
        repo_name = ""
        for proj in self.config.projects.values():
            repo_name = proj.repo
            break

        tasks = [server.serve()]
        if self.config.github.mode == "poll" and repo_name:
            tasks.append(self.webhook.poll_loop(repo_name))

        await asyncio.gather(*tasks)
