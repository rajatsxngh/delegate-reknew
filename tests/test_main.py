"""Tests for reknew.main -- pipeline entry point."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from reknew.main import (
    _build_agent_prompt,
    _check_prerequisites,
    _clone_repo,
    _commit_changes,
    _ensure_dirs,
    REKNEW_HOME,
    run_single_issue,
)


@pytest.fixture
def fake_reknew_yaml(tmp_path, monkeypatch):
    """Write a minimal valid reknew.yaml and chdir to tmp_path."""
    data = {
        "projects": {
            "test": {
                "repo": "owner/repo",
                "default_branch": "main",
                "test_command": "pytest -x",
            }
        },
    }
    cfg_path = tmp_path / "reknew.yaml"
    cfg_path.write_text(yaml.dump(data))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# -- _ensure_dirs --

def test_ensure_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("reknew.main.REKNEW_HOME", tmp_path / ".reknew")
    monkeypatch.setattr("reknew.main.REPOS_DIR", tmp_path / ".reknew" / "repos")
    monkeypatch.setattr("reknew.main.WORKTREES_DIR", tmp_path / ".reknew" / "worktrees")
    monkeypatch.setattr("reknew.main.LOGS_DIR", tmp_path / ".reknew" / "logs")
    _ensure_dirs()
    assert (tmp_path / ".reknew" / "repos").is_dir()
    assert (tmp_path / ".reknew" / "worktrees").is_dir()
    assert (tmp_path / ".reknew" / "logs").is_dir()


# -- _check_prerequisites --

def test_check_prerequisites_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
    assert _check_prerequisites() is False


def test_check_prerequisites_all_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/" + x)
    assert _check_prerequisites() is True


# -- _clone_repo --

def test_clone_repo_existing(tmp_path, monkeypatch):
    """If clone already exists, just pull."""
    monkeypatch.setattr("reknew.main.REPOS_DIR", tmp_path)
    clone_dir = tmp_path / "owner-repo"
    clone_dir.mkdir()

    with patch("reknew.main.subprocess.run") as mock_run:
        result = _clone_repo("owner/repo", "main")
        assert result == clone_dir
        mock_run.assert_called_once()
        assert "pull" in mock_run.call_args[0][0]


def test_clone_repo_fresh(tmp_path, monkeypatch):
    """If clone doesn't exist, git clone."""
    monkeypatch.setattr("reknew.main.REPOS_DIR", tmp_path)

    with patch("reknew.main.subprocess.run") as mock_run:
        result = _clone_repo("owner/repo", "main")
        assert result == tmp_path / "owner-repo"
        mock_run.assert_called_once()
        assert "clone" in mock_run.call_args[0][0]


# -- _build_agent_prompt --

def test_build_agent_prompt(fake_reknew_yaml):
    """Agent prompt includes issue details and instructions."""
    from reknew.config import load_config
    from reknew.connectors.github_connector import IssueContext

    cfg = load_config()
    ctx = IssueContext(
        issue_number=1,
        title="Add update endpoint",
        body="We need PUT /tasks/{id}",
        labels=["enhancement"],
        comments=["This should also validate input"],
        repo_name="owner/repo",
        default_branch="main",
        file_tree=["taskflow/main.py", "taskflow/models.py"],
        readme_content="# TaskFlow\nA simple task API",
        related_files=[],
    )

    prompt = _build_agent_prompt(ctx, cfg)
    assert "Issue #1" in prompt
    assert "Add update endpoint" in prompt
    assert "PUT /tasks/{id}" in prompt
    assert "enhancement" in prompt
    assert "validate input" in prompt
    assert "taskflow/main.py" in prompt
    assert "TaskFlow" in prompt
    assert "pytest -x" in prompt
    assert "Instructions" in prompt


# -- _commit_changes --

def test_commit_changes_no_changes(tmp_path):
    """No commit when there are no changes."""
    # Create a git repo with one commit
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, capture_output=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=tmp_path, capture_output=True)

    assert _commit_changes(tmp_path, "no-op") is False


def test_commit_changes_with_changes(tmp_path):
    """Commits when there are changes."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, capture_output=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=tmp_path, capture_output=True)

    # Make a change
    (tmp_path / "new.txt").write_text("new file")
    assert _commit_changes(tmp_path, "add new file") is True

    # Verify commit was made
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert "add new file" in result.stdout


# -- run_single_issue (integration-style, heavily mocked) --

def test_run_single_issue_pipeline(fake_reknew_yaml, monkeypatch, tmp_path):
    """Run the full pipeline with all externals mocked."""
    repos_dir = tmp_path / "repos"
    worktrees_dir = tmp_path / "worktrees"
    logs_dir = tmp_path / "logs"
    repos_dir.mkdir()
    worktrees_dir.mkdir()
    logs_dir.mkdir()

    monkeypatch.setattr("reknew.main.REPOS_DIR", repos_dir)
    monkeypatch.setattr("reknew.main.WORKTREES_DIR", worktrees_dir)
    monkeypatch.setattr("reknew.main.LOGS_DIR", logs_dir)

    # Mock state writes so we don't touch the real state file
    monkeypatch.setattr("reknew.main.state.update_task", lambda *a, **kw: None)
    monkeypatch.setattr("reknew.main.state.update_capacity", lambda *a, **kw: None)

    # Mock GitHubConnector
    mock_ctx = MagicMock()
    mock_ctx.title = "Test Issue"
    mock_ctx.labels = ["bug"]
    mock_ctx.file_tree = ["README.md"]
    mock_ctx.body = "Fix the bug"
    mock_ctx.comments = []
    mock_ctx.readme_content = ""
    mock_ctx.related_files = []
    mock_ctx.repo_name = "owner/repo"
    mock_connector = MagicMock()
    mock_connector.fetch_issue.return_value = mock_ctx

    with patch("reknew.main.GitHubConnector", return_value=mock_connector):
        clone_dir = repos_dir / "owner-repo"
        clone_dir.mkdir()

        with patch("reknew.main._clone_repo", return_value=clone_dir):
            wt_path = worktrees_dir / "T0001"
            wt_path.mkdir()

            with patch(
                "reknew.main._create_worktree",
                return_value=(wt_path, "reknew/task/T0001"),
            ):
                mock_process = MagicMock()
                mock_process.poll.side_effect = [None, 0]
                mock_process.returncode = 0
                mock_process.pid = 12345

                with patch(
                    "reknew.main._spawn_agent",
                    return_value=mock_process,
                ):
                    with patch("reknew.main._commit_changes", return_value=False):
                        with patch("reknew.main.subprocess.run") as mock_subp:
                            mock_subp.return_value = MagicMock(
                                stdout="", returncode=0
                            )
                            with patch("asyncio.sleep", new_callable=AsyncMock):
                                asyncio.run(
                                    run_single_issue("owner/repo", 1)
                                )

    mock_connector.fetch_issue.assert_called_once_with("owner/repo", 1)
