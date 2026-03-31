"""Tests for reknew.dashboard.api — FastAPI routes."""

from unittest.mock import MagicMock

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


# ── Health ────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "uptime_seconds" in data


# ── Config ────────────────────────────────────────────────────────────────

def test_config_endpoint(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert "slayer" in data["projects"]
    assert data["defaults"]["agent"] == "claude-code"
    # Secrets should not be present
    assert "webhook_secret" not in data.get("github", {})


# ── Projects ──────────────────────────────────────────────────────────────

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
    data = resp.json()
    assert "error" in data


# ── Tasks ─────────────────────────────────────────────────────────────────

def test_list_tasks_no_daemon(client):
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_task_not_found(client):
    resp = client.get("/api/tasks/T9999")
    assert resp.status_code == 200
    assert resp.json() == {"error": "Task not found"}


# ── Capacity ──────────────────────────────────────────────────────────────

def test_capacity_no_daemon(client):
    resp = client.get("/api/capacity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


def test_capacity_history(client):
    resp = client.get("/api/capacity/history")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Agents ────────────────────────────────────────────────────────────────

def test_agents_no_daemon(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []


# ── PRs ───────────────────────────────────────────────────────────────────

def test_prs_no_daemon(client):
    resp = client.get("/api/prs")
    assert resp.status_code == 200
    assert resp.json() == []


# ── With daemon ───────────────────────────────────────────────────────────

def test_tasks_with_daemon(config):
    mock_daemon = MagicMock()
    mock_task = MagicMock()
    mock_task.to_dict.return_value = {
        "id": "T0001",
        "title": "Test",
        "state": "in_progress",
    }
    mock_daemon.tasks = {"T0001": mock_task}

    app = create_app(config, daemon=mock_daemon)
    client = TestClient(app)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "T0001"


def test_tasks_filter_state(config):
    mock_daemon = MagicMock()
    t1 = MagicMock()
    t1.to_dict.return_value = {"id": "T0001", "state": "in_progress"}
    t2 = MagicMock()
    t2.to_dict.return_value = {"id": "T0002", "state": "done"}
    mock_daemon.tasks = {"T0001": t1, "T0002": t2}

    app = create_app(config, daemon=mock_daemon)
    client = TestClient(app)
    resp = client.get("/api/tasks?state=done")
    tasks = resp.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "T0002"
