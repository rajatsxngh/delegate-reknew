"""FastAPI routes for the ReKnew dashboard."""

import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from reknew.config import ReknewConfig
from reknew.dashboard.websocket import EventBroadcaster

VERSION = "0.1.0"


def create_app(config: ReknewConfig, daemon: Any = None) -> FastAPI:
    """Create the FastAPI app with all routes.

    Args:
        config: validated ReknewConfig
        daemon: optional ReknewDaemon instance for runtime access

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(title="ReKnew Dashboard", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    start_time = time.time()
    broadcaster = EventBroadcaster()

    # Expose broadcaster on app state for external access
    app.state.broadcaster = broadcaster

    # ── Health ────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": VERSION,
            "uptime_seconds": int(time.time() - start_time),
        }

    # ── Config ────────────────────────────────────────────────────────

    @app.get("/api/config")
    def get_config() -> dict:
        """Return serialized config with secrets redacted."""
        projects = {}
        for name, p in config.projects.items():
            projects[name] = {
                "repo": p.repo,
                "default_branch": p.default_branch,
                "test_command": p.test_command,
            }
        return {
            "projects": projects,
            "defaults": {
                "agent": config.defaults.agent,
                "workspace": config.defaults.workspace,
                "max_parallel_agents": config.defaults.max_parallel_agents,
                "agent_timeout": config.defaults.agent_timeout,
            },
            "github": {
                "auto_label_prs": config.github.auto_label_prs,
                "pr_prefix": config.github.pr_prefix,
                "poll_interval": config.github.poll_interval,
                "mode": config.github.mode,
            },
            "port": config.port,
        }

    # ── Projects ──────────────────────────────────────────────────────

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return [
            {"name": name, "repo": p.repo, "default_branch": p.default_branch}
            for name, p in config.projects.items()
        ]

    @app.get("/api/projects/{name}/issues")
    def list_project_issues(name: str) -> dict:
        if name not in config.projects:
            return {"error": f"Project '{name}' not found"}
        if daemon and hasattr(daemon, "connector"):
            repo = config.projects[name].repo
            try:
                gh_repo = daemon.connector.gh.get_repo(repo)
                issues = []
                for issue in gh_repo.get_issues(state="open")[:25]:
                    issues.append({
                        "number": issue.number,
                        "title": issue.title,
                        "labels": [l.name for l in issue.labels],
                    })
                return {"issues": issues}
            except Exception as exc:
                return {"error": str(exc)}
        return {"issues": [], "note": "daemon not connected"}

    @app.post("/api/projects/{name}/issues/{number}/process")
    async def process_issue(name: str, number: int) -> dict:
        if name not in config.projects:
            return {"error": f"Project '{name}' not found"}
        if daemon and hasattr(daemon, "process_issue"):
            task_ids = await daemon.process_issue(name, number)
            return {"task_ids": task_ids, "status": "processing"}
        return {"status": "daemon not available"}

    # ── Tasks ─────────────────────────────────────────────────────────

    @app.get("/api/tasks")
    def list_tasks(
        state: str | None = Query(None),
        project: str | None = Query(None),
    ) -> list[dict]:
        if daemon and hasattr(daemon, "tasks"):
            tasks = list(daemon.tasks.values())
            result = []
            for t in tasks:
                d = t.to_dict() if hasattr(t, "to_dict") else t
                if state and d.get("state") != state:
                    continue
                result.append(d)
            return result
        return []

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        if daemon and hasattr(daemon, "tasks"):
            task = daemon.tasks.get(task_id)
            if task:
                return task.to_dict() if hasattr(task, "to_dict") else task
        return {"error": "Task not found"}

    # ── Capacity ──────────────────────────────────────────────────────

    @app.get("/api/capacity")
    def get_capacity() -> dict:
        if daemon and hasattr(daemon, "router"):
            decisions = {}
            for tid, task in daemon.tasks.items():
                if hasattr(task, "routing_decision") and task.routing_decision:
                    decisions[tid] = task.routing_decision
            summary = daemon.router.get_capacity_summary(decisions)
            summary["compression_ratio"] = "2 weeks -> 30 minutes"
            return summary
        return {
            "total": 0,
            "human": 0,
            "ai": 0,
            "human_tasks": [],
            "ai_tasks": [],
            "rules_matched": {},
        }

    @app.get("/api/capacity/history")
    def get_capacity_history() -> list[dict]:
        # Placeholder — history requires persistent storage
        return []

    # ── Agents ────────────────────────────────────────────────────────

    @app.get("/api/agents")
    def list_agents() -> list[dict]:
        if daemon and hasattr(daemon, "spawner"):
            statuses = daemon.spawner.get_all_statuses()
            result = []
            for tid, status in statuses.items():
                info = daemon.spawner.running.get(tid, {})
                result.append({
                    "task_id": tid,
                    "agent_type": config.defaults.agent,
                    "status": status.value,
                    "worktree": info.get("worktree", ""),
                })
            return result
        return []

    # ── PRs ───────────────────────────────────────────────────────────

    @app.get("/api/prs")
    def list_prs() -> list[dict]:
        if daemon and hasattr(daemon, "tasks"):
            prs = []
            for tid, task in daemon.tasks.items():
                d = task.to_dict() if hasattr(task, "to_dict") else task
                if d.get("pr_number"):
                    prs.append({
                        "pr_number": d["pr_number"],
                        "title": d["title"],
                        "ci_status": d.get("ci_status", ""),
                        "state": d.get("state", ""),
                        "task_id": tid,
                    })
            return prs
        return []

    # ── WebSocket ─────────────────────────────────────────────────────

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        await broadcaster.connect(websocket)
        try:
            while True:
                # Keep connection alive; client doesn't send data
                await websocket.receive_text()
        except WebSocketDisconnect:
            await broadcaster.disconnect(websocket)

    return app
