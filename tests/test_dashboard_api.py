"""Tests for reknew.dashboard.api -- FastAPI routes."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from reknew.config import (
    CapacityConfig,
    DefaultsConfig,
    GithubConfig,
    ProjectConfig,
    ReknewConfig,
)
from reknew.dashboard.api import create_app

EMPTY_STATE = {"tasks": {}, "capacity": {"total": 0, "human": 0, "ai": 0}, "updated_at": 0}


@pytest.fixture
def config():
    return ReknewConfig(
        projects={"slayer": ProjectConfig(repo="owner/repo")},
        defaults=DefaultsConfig(),
        reactions={},
        capacity=CapacityConfig(),
        github=GithubConfig(),
    )


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


# -- Health ----------------------------------------------------------------

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "uptime_seconds" in data


# -- Config ----------------------------------------------------------------

def test_config_endpoint(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert "slayer" in data["projects"]
    assert data["defaults"]["agent"] == "claude-code"
    assert "webhook_secret" not in data.get("github", {})


# -- Projects --------------------------------------------------------------

def test_list_projects(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    projects = resp.json()
    assert len(projects) == 1
    assert projects[0]["name"] == "slayer"
    assert projects[0]["repo"] == "owner/repo"


def test_list_project_issues_no_daemon(client):
    resp = client.get("/api/projects/slayer/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("note") == "daemon not connected"


def test_list_project_issues_not_found(client):
    resp = client.get("/api/projects/nonexistent/issues")
    assert resp.status_code == 200
    assert "error" in resp.json()


# -- Tasks (reads from state.json) -----------------------------------------

def test_list_tasks_empty(client):
    with patch("reknew.dashboard.api.state.read_state", return_value=EMPTY_STATE):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json() == []


def test_get_task_not_found(client):
    with patch("reknew.dashboard.api.state.read_state", return_value=EMPTY_STATE):
        resp = client.get("/api/tasks/T9999")
        assert resp.status_code == 200
        assert resp.json() == {"error": "Task not found"}


def test_list_tasks_with_state(client):
    data = {
        "tasks": {
            "T0001": {"id": "T0001", "title": "Test", "state": "agent_running"},
            "T0002": {"id": "T0002", "title": "Other", "state": "agent_done"},
        },
        "capacity": {"total": 2, "human": 0, "ai": 2},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/tasks")
        assert len(resp.json()) == 2


def test_list_tasks_filter_state(client):
    data = {
        "tasks": {
            "T0001": {"id": "T0001", "state": "agent_running"},
            "T0002": {"id": "T0002", "state": "agent_done"},
        },
        "capacity": {"total": 2, "human": 0, "ai": 2},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/tasks?state=agent_done")
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "T0002"


def test_get_task_found(client):
    data = {
        "tasks": {"T0001": {"id": "T0001", "title": "Test", "state": "fetched"}},
        "capacity": {},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/tasks/T0001")
        assert resp.json()["title"] == "Test"


# -- Capacity (reads from state.json) --------------------------------------

def test_capacity_empty(client):
    with patch("reknew.dashboard.api.state.read_state", return_value=EMPTY_STATE):
        resp = client.get("/api/capacity")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


def test_capacity_with_data(client):
    data = {
        "tasks": {},
        "capacity": {"total": 5, "human": 1, "ai": 4},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/capacity")
        d = resp.json()
        assert d["total"] == 5
        assert d["ai"] == 4


def test_capacity_history(client):
    resp = client.get("/api/capacity/history")
    assert resp.status_code == 200
    assert resp.json() == []


# -- Agents (reads from state.json) ----------------------------------------

def test_agents_empty(client):
    with patch("reknew.dashboard.api.state.read_state", return_value=EMPTY_STATE):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        assert resp.json() == []


def test_agents_running(client):
    data = {
        "tasks": {
            "T0001": {"id": "T0001", "state": "agent_running", "agent_type": "claude-code",
                       "worktree": "/tmp/wt", "log_bytes": 1024, "pid": 12345},
        },
        "capacity": {},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/agents")
        agents = resp.json()
        assert len(agents) == 1
        assert agents[0]["task_id"] == "T0001"
        assert agents[0]["pid"] == 12345


# -- PRs (reads from state.json) -------------------------------------------

def test_prs_empty(client):
    with patch("reknew.dashboard.api.state.read_state", return_value=EMPTY_STATE):
        resp = client.get("/api/prs")
        assert resp.status_code == 200
        assert resp.json() == []


def test_prs_with_data(client):
    data = {
        "tasks": {
            "T0001": {"id": "T0001", "title": "Fix", "state": "pr_created",
                       "pr_number": 42, "pr_url": "https://github.com/a/b/pull/42",
                       "ci_status": "passed"},
        },
        "capacity": {},
        "updated_at": 0,
    }
    with patch("reknew.dashboard.api.state.read_state", return_value=data):
        resp = client.get("/api/prs")
        prs = resp.json()
        assert len(prs) == 1
        assert prs[0]["pr_number"] == 42
        assert prs[0]["pr_url"] == "https://github.com/a/b/pull/42"


# -- Process direct --------------------------------------------------------

def test_process_direct_missing_fields(client):
    resp = client.post("/api/process", json={})
    assert resp.json()["error"] == "repo and issue_number are required"


# -- Frontend ---------------------------------------------------------------

def test_frontend_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ReKnew" in resp.text
