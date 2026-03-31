"""Tests for reknew.main — Phase 1 pipeline entry point."""

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from reknew.main import (
    _check_prerequisites,
    _clone_repo,
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
            }
        },
    }
    cfg_path = tmp_path / "reknew.yaml"
    cfg_path.write_text(yaml.dump(data))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── _ensure_dirs ──────────────────────────────────────────────────────────

def test_ensure_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("reknew.main.REKNEW_HOME", tmp_path / ".reknew")
    monkeypatch.setattr("reknew.main.REPOS_DIR", tmp_path / ".reknew" / "repos")
    monkeypatch.setattr("reknew.main.WORKTREES_DIR", tmp_path / ".reknew" / "worktrees")
    monkeypatch.setattr("reknew.main.LOGS_DIR", tmp_path / ".reknew" / "logs")
    _ensure_dirs()
    assert (tmp_path / ".reknew" / "repos").is_dir()
    assert (tmp_path / ".reknew" / "worktrees").is_dir()
    assert (tmp_path / ".reknew" / "logs").is_dir()


# ── _check_prerequisites ─────────────────────────────────────────────────

def test_check_prerequisites_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
    assert _check_prerequisites() is False


def test_check_prerequisites_all_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/" + x)
    assert _check_prerequisites() is True


# ── _clone_repo ───────────────────────────────────────────────────────────

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


# ── run_single_issue (integration-style, heavily mocked) ─────────────────

def test_run_single_issue_pipeline(fake_reknew_yaml, monkeypatch, tmp_path):
    """Run the full pipeline with all externals mocked."""
    import asyncio

    repos_dir = tmp_path / "repos"
    worktrees_dir = tmp_path / "worktrees"
    logs_dir = tmp_path / "logs"
    repos_dir.mkdir()
    worktrees_dir.mkdir()
    logs_dir.mkdir()

    monkeypatch.setattr("reknew.main.REPOS_DIR", repos_dir)
    monkeypatch.setattr("reknew.main.WORKTREES_DIR", worktrees_dir)
    monkeypatch.setattr("reknew.main.LOGS_DIR", logs_dir)

    # Mock GitHubConnector
    mock_ctx = MagicMock()
    mock_ctx.title = "Test Issue"
    mock_ctx.labels = ["bug"]
    mock_ctx.file_tree = ["README.md"]
    mock_connector = MagicMock()
    mock_connector.fetch_issue.return_value = mock_ctx
    mock_connector.format_for_openspec.return_value = "# Issue #1: Test"

    with patch("reknew.main.GitHubConnector", return_value=mock_connector):
        # Mock clone (create the dir so bridge works)
        clone_dir = repos_dir / "owner-repo"
        clone_dir.mkdir()

        with patch("reknew.main._clone_repo", return_value=clone_dir):
            # Mock OpenSpecBridge
            mock_bridge = MagicMock()
            mock_bridge.get_agent_prompt.return_value = "do stuff"
            mock_bridge.create_change_proposal.return_value = (
                clone_dir / "openspec" / "changes" / "issue-1"
            )

            with patch("reknew.main.OpenSpecBridge", return_value=mock_bridge):
                # Mock worktree creation
                wt_path = worktrees_dir / "T0001"
                wt_path.mkdir()

                with patch(
                    "reknew.main._create_worktree",
                    return_value=(wt_path, "reknew/task/T0001"),
                ):
                    # Mock agent process
                    mock_process = MagicMock()
                    mock_process.poll.side_effect = [None, 0]
                    mock_process.returncode = 0

                    with patch(
                        "reknew.main._spawn_agent",
                        return_value=mock_process,
                    ):
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            asyncio.run(run_single_issue("owner/repo", 1))

    # Verify the pipeline called all stages
    mock_connector.fetch_issue.assert_called_once_with("owner/repo", 1)
    mock_connector.format_for_openspec.assert_called_once()
    mock_bridge.create_change_proposal.assert_called_once()
    mock_bridge.get_agent_prompt.assert_called_once()
