"""Tests for reknew.github.pr_manager — PR creation, CI, reviews, merge."""

from unittest.mock import MagicMock, patch

import pytest

from reknew.config import (
    DefaultsConfig,
    CapacityConfig,
    GithubConfig,
    ReknewConfig,
    ProjectConfig,
)
from reknew.github.api import GitHubAPI
from reknew.github.pr_manager import (
    CIStatus,
    PRManager,
    PRResult,
    ReviewComment,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return ReknewConfig(
        projects={"test": ProjectConfig(repo="owner/repo")},
        defaults=DefaultsConfig(),
        reactions={},
        capacity=CapacityConfig(),
        github=GithubConfig(
            auto_label_prs=True,
            pr_prefix="[ReKnew]",
        ),
    )


@pytest.fixture
def mock_api():
    with patch("reknew.github.api.Github"):
        api = GitHubAPI(token="fake-token")
    return api


@pytest.fixture
def pr_manager(mock_api, config):
    return PRManager(mock_api, config)


# ── create_pr ─────────────────────────────────────────────────────────────

def test_create_pr(pr_manager, mock_api):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.number = 42
    mock_pr.html_url = "https://github.com/owner/repo/pull/42"
    mock_repo.create_pull.return_value = mock_pr
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    result = pr_manager.create_pr(
        repo_name="owner/repo",
        branch="reknew/task/T0001",
        base="main",
        title="Add config metadata",
        body="Implements the config metadata feature.",
        task_id="T0001",
    )

    assert isinstance(result, PRResult)
    assert result.pr_number == 42
    assert result.pr_url == "https://github.com/owner/repo/pull/42"
    assert result.title == "[ReKnew] Add config metadata"
    mock_repo.create_pull.assert_called_once()
    call_kwargs = mock_repo.create_pull.call_args[1]
    assert call_kwargs["title"] == "[ReKnew] Add config metadata"
    assert "T0001" in call_kwargs["body"]


def test_create_pr_with_label(pr_manager, mock_api):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.number = 10
    mock_pr.html_url = "https://github.com/owner/repo/pull/10"
    mock_repo.create_pull.return_value = mock_pr
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    pr_manager.create_pr(
        repo_name="owner/repo",
        branch="b",
        base="main",
        title="X",
        body="Y",
        task_id="T0001",
    )
    mock_pr.add_to_labels.assert_called_once_with("reknew-ai")


def test_create_pr_with_issue_link(pr_manager, mock_api):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.number = 5
    mock_pr.html_url = "https://github.com/owner/repo/pull/5"
    mock_repo.create_pull.return_value = mock_pr
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    pr_manager.create_pr(
        repo_name="owner/repo",
        branch="b",
        base="main",
        title="Fix",
        body="desc",
        task_id="T0001",
        issue_number=114,
    )
    call_kwargs = mock_repo.create_pull.call_args[1]
    assert "#114" in call_kwargs["body"]


# ── get_ci_status ─────────────────────────────────────────────────────────

def _mock_check_run(name, conclusion):
    run = MagicMock()
    run.name = name
    run.conclusion = conclusion
    run.output = {"summary": f"{name} output"}
    run.html_url = f"https://github.com/runs/{name}"
    return run


def test_get_ci_status_all_passed(pr_manager, mock_api):
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123"
    mock_api.get_pull = MagicMock(return_value=mock_pr)

    mock_commit = MagicMock()
    mock_commit.get_check_runs.return_value = [
        _mock_check_run("pytest", "success"),
        _mock_check_run("lint", "success"),
    ]
    mock_commit.get_statuses.return_value = []
    mock_repo = MagicMock()
    mock_repo.get_commit.return_value = mock_commit
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    ci = pr_manager.get_ci_status("owner/repo", 1)
    assert ci.overall == "passed"
    assert len(ci.checks) == 2
    assert ci.failed_checks == []


def test_get_ci_status_some_failed(pr_manager, mock_api):
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123"
    mock_api.get_pull = MagicMock(return_value=mock_pr)

    mock_commit = MagicMock()
    mock_commit.get_check_runs.return_value = [
        _mock_check_run("pytest", "success"),
        _mock_check_run("lint", "failure"),
    ]
    mock_commit.get_statuses.return_value = []
    mock_repo = MagicMock()
    mock_repo.get_commit.return_value = mock_commit
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    ci = pr_manager.get_ci_status("owner/repo", 1)
    assert ci.overall == "failed"
    assert len(ci.failed_checks) == 1
    assert ci.failed_checks[0]["name"] == "lint"


def test_get_ci_status_pending(pr_manager, mock_api):
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123"
    mock_api.get_pull = MagicMock(return_value=mock_pr)

    mock_commit = MagicMock()
    mock_commit.get_check_runs.return_value = [
        _mock_check_run("pytest", None),  # still running
    ]
    mock_commit.get_statuses.return_value = []
    mock_repo = MagicMock()
    mock_repo.get_commit.return_value = mock_commit
    mock_api.get_repo = MagicMock(return_value=mock_repo)

    ci = pr_manager.get_ci_status("owner/repo", 1)
    assert ci.overall == "pending"


# ── get_review_comments ───────────────────────────────────────────────────

def test_get_review_comments(pr_manager, mock_api):
    mock_review = MagicMock()
    mock_review.state = "CHANGES_REQUESTED"
    mock_review.body = "Please fix the error handling"
    mock_review.user.login = "john-doe"
    mock_review.submitted_at = "2025-01-01T00:00:00Z"

    mock_inline = MagicMock()
    mock_inline.user.login = "jane-smith"
    mock_inline.body = "Use a dataclass here"
    mock_inline.path = "src/models.py"
    mock_inline.original_line = 42
    mock_inline.created_at = "2025-01-01T01:00:00Z"

    mock_pr = MagicMock()
    mock_pr.get_reviews.return_value = [mock_review]
    mock_pr.get_review_comments.return_value = [mock_inline]
    mock_api.get_pull = MagicMock(return_value=mock_pr)

    comments = pr_manager.get_review_comments("owner/repo", 1)
    assert len(comments) == 2
    assert comments[0].reviewer == "john-doe"
    assert comments[0].state == "CHANGES_REQUESTED"
    assert comments[1].reviewer == "jane-smith"
    assert comments[1].path == "src/models.py"
    assert comments[1].line == 42


# ── format helpers ────────────────────────────────────────────────────────

def test_format_ci_errors(pr_manager):
    ci = CIStatus(
        overall="failed",
        checks=[
            {"name": "pytest", "state": "failure",
             "description": "3 tests failed", "url": "https://log/pytest"},
            {"name": "lint", "state": "failure",
             "description": "5 lint errors", "url": "https://log/lint"},
        ],
        failed_checks=[
            {"name": "pytest", "state": "failure",
             "description": "3 tests failed", "url": "https://log/pytest"},
            {"name": "lint", "state": "failure",
             "description": "5 lint errors", "url": "https://log/lint"},
        ],
    )
    output = pr_manager.format_ci_errors(ci)
    assert "CI failed" in output
    assert "pytest" in output
    assert "lint" in output
    assert "3 tests failed" in output
    assert "https://log/pytest" in output


def test_format_review_comments(pr_manager):
    comments = [
        ReviewComment(
            reviewer="john-doe",
            body="Fix error handling",
            state="CHANGES_REQUESTED",
        ),
        ReviewComment(
            reviewer="jane-smith",
            body="Use a dataclass",
            state="INLINE",
            path="src/models.py",
            line=42,
        ),
    ]
    output = pr_manager.format_review_comments(comments)
    assert "john-doe" in output
    assert "jane-smith" in output
    assert "src/models.py" in output
    assert "line 42" in output
    assert "Fix error handling" in output


# ── push_branch ───────────────────────────────────────────────────────────

def test_push_branch(pr_manager):
    with patch("reknew.github.pr_manager.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        pr_manager.push_branch("/repo", "feature-branch")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "push", "origin", "feature-branch"]


def test_push_branch_force_with_lease(pr_manager):
    with patch("reknew.github.pr_manager.subprocess.run") as mock_run:
        # First push fails, second succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="rejected"),
            MagicMock(returncode=0),
        ]
        pr_manager.push_branch("/repo", "feature-branch")
        assert mock_run.call_count == 2
        second_cmd = mock_run.call_args_list[1][0][0]
        assert "--force-with-lease" in second_cmd


# ── merge_pr ──────────────────────────────────────────────────────────────

def test_merge_pr(pr_manager, mock_api):
    mock_pr = MagicMock()
    mock_api.get_pull = MagicMock(return_value=mock_pr)

    result = pr_manager.merge_pr("owner/repo", 42)
    assert result is True
    mock_pr.merge.assert_called_once_with(merge_method="squash")
