"""FastAPI routes for the ReKnew dashboard."""

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from reknew.config import ReknewConfig
from reknew.dashboard.websocket import EventBroadcaster
from reknew import state

VERSION = "0.1.0"
FRONTEND_DIR = Path(__file__).parent / "frontend"


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

    app.state.broadcaster = broadcaster

    # Track background pipeline runs
    _running_pipelines: dict[str, dict] = {}

    # -- Health ---------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": VERSION,
            "uptime_seconds": int(time.time() - start_time),
        }

    # -- Config ---------------------------------------------------------

    @app.get("/api/config")
    def get_config() -> dict:
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

    # -- Projects -------------------------------------------------------

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return [
            {
                "name": name,
                "repo": p.repo,
                "default_branch": p.default_branch,
            }
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

    # -- Process (direct, no daemon needed) -----------------------------

    @app.post("/api/process")
    async def process_direct(body: dict) -> dict:
        """Run pipeline from dashboard UI. Runs in a background thread.

        Expects JSON body: {"repo": "owner/repo", "issue_number": 1}
        """
        repo = body.get("repo", "")
        issue_number = body.get("issue_number")
        if not repo or not issue_number:
            return {"error": "repo and issue_number are required"}

        key = f"{repo}#{issue_number}"
        if key in _running_pipelines:
            return {"status": "already_running", "key": key}

        def _run() -> None:
            from reknew.main import (
                _check_prerequisites,
                _ensure_dirs,
                run_single_issue,
            )
            _ensure_dirs()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    run_single_issue(repo, int(issue_number))
                )
                _running_pipelines[key]["status"] = "done"
            except Exception as exc:
                _running_pipelines[key]["status"] = f"error: {exc}"
            finally:
                loop.close()

        _running_pipelines[key] = {
            "status": "running",
            "repo": repo,
            "issue_number": issue_number,
            "started_at": time.time(),
        }
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return {"status": "started", "key": key}

    @app.get("/api/pipelines")
    def list_pipelines() -> list[dict]:
        """List running/completed pipeline runs."""
        return [
            {"key": k, **v}
            for k, v in _running_pipelines.items()
        ]

    # -- Tasks (reads from shared state) --------------------------------

    @app.get("/api/tasks")
    def list_tasks(
        state_filter: str | None = Query(None, alias="state"),
    ) -> list[dict]:
        data = state.read_state()
        tasks = list(data.get("tasks", {}).values())
        if state_filter:
            tasks = [t for t in tasks if t.get("state") == state_filter]
        return tasks

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        data = state.read_state()
        task = data.get("tasks", {}).get(task_id)
        if task:
            return task
        return {"error": "Task not found"}

    # -- Capacity (reads from shared state) -----------------------------

    @app.get("/api/capacity")
    def get_capacity() -> dict:
        data = state.read_state()
        cap = data.get("capacity", {})
        return {
            "total": cap.get("total", 0),
            "human": cap.get("human", 0),
            "ai": cap.get("ai", 0),
            "human_tasks": [],
            "ai_tasks": [],
            "rules_matched": {},
            "compression_ratio": "2 weeks -> 30 minutes",
        }

    @app.get("/api/capacity/history")
    def get_capacity_history() -> list[dict]:
        return []

    # -- Agents (reads from shared state) -------------------------------

    @app.get("/api/agents")
    def list_agents() -> list[dict]:
        data = state.read_state()
        agents = []
        for tid, task in data.get("tasks", {}).items():
            if task.get("state") in ("agent_running", "worktree_created"):
                agents.append({
                    "task_id": tid,
                    "agent_type": task.get("agent_type", "claude-code"),
                    "status": "running",
                    "worktree": task.get("worktree", ""),
                    "log_bytes": task.get("log_bytes", 0),
                    "pid": task.get("pid"),
                })
        return agents

    # -- PRs (reads from shared state) ----------------------------------

    @app.get("/api/prs")
    def list_prs() -> list[dict]:
        data = state.read_state()
        prs = []
        for tid, task in data.get("tasks", {}).items():
            if task.get("pr_number"):
                prs.append({
                    "task_id": tid,
                    "pr_number": task["pr_number"],
                    "pr_url": task.get("pr_url", ""),
                    "title": task.get("title", ""),
                    "ci_status": task.get("ci_status", "pending"),
                    "state": task.get("state", ""),
                })
        return prs

    # -- WebSocket ------------------------------------------------------

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        await broadcaster.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await broadcaster.disconnect(websocket)

    # -- Frontend -------------------------------------------------------

    @app.get("/")
    def serve_frontend() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    return app
