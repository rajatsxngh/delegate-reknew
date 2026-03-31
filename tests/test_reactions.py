"""Tests for reknew.reactions.engine — config-driven reactions."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from reknew.config import (
    CapacityConfig,
    DefaultsConfig,
    GithubConfig,
    ProjectConfig,
    ReactionRule,
    ReknewConfig,
)
from reknew.github.pr_manager import CIStatus, ReviewComment
from reknew.reactions.engine import ReactionsEngine


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_config(
    ci_auto=True,
    ci_retries=2,
    ci_max_cost=5.0,
    changes_auto=True,
    approved_auto=False,
    approved_action="notify",
):
    reactions = {}
    reactions["ci-failed"] = ReactionRule(
        auto=ci_auto, retries=ci_retries,
        max_cost=ci_max_cost, action="send-to-agent",
    )
    reactions["changes-requested"] = ReactionRule(
        auto=changes_auto, action="send-to-agent",
    )
    reactions["approved-and-green"] = ReactionRule(
        auto=approved_auto, action=approved_action,
    )
    return ReknewConfig(
        projects={"t": ProjectConfig(repo="owner/repo")},
        defaults=DefaultsConfig(),
        reactions=reactions,
        capacity=CapacityConfig(),
        github=GithubConfig(),
    )


@pytest.fixture
def mock_pr_manager():
    mgr = MagicMock()
    mgr.get_ci_status.return_value = CIStatus(
        overall="failed",
        checks=[{"name": "pytest", "state": "failure",
                 "description": "fail", "url": ""}],
        failed_checks=[{"name": "pytest", "state": "failure",
                        "description": "fail", "url": ""}],
    )
    mgr.format_ci_errors.return_value = "CI failed: pytest"
    mgr.get_review_comments.return_value = [
        ReviewComment(
            reviewer="alice", body="Fix this",
            state="CHANGES_REQUESTED",
        ),
    ]
    mgr.format_review_comments.return_value = "Review: fix this"
    return mgr


@pytest.fixture
def mock_spawner():
    spawner = MagicMock()
    mock_adapter = MagicMock()
    spawner.running = {
        "T0001": {"handle": "T0001", "adapter": mock_adapter},
    }
    return spawner


# ── Tests ─────────────────────────────────────────────────────────────────

def test_ci_failed_retries(mock_pr_manager, mock_spawner):
    """Verify agent re-prompted on first failure."""
    cfg = _make_config(ci_auto=True, ci_retries=2)
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    adapter = mock_spawner.running["T0001"]["adapter"]
    adapter.send_feedback.assert_called_once()
    assert engine._task_retry_counts["T0001"] == 1


def test_ci_failed_max_retries(mock_pr_manager, mock_spawner):
    """Verify escalation after max retries exceeded."""
    cfg = _make_config(ci_auto=True, ci_retries=2)
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)
    engine._task_retry_counts["T0001"] = 2  # already exhausted

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    mock_pr_manager.close_pr.assert_called_once()
    adapter = mock_spawner.running["T0001"]["adapter"]
    adapter.send_feedback.assert_not_called()


def test_ci_failed_cost_limit(mock_pr_manager, mock_spawner):
    """Verify escalation when max_cost exceeded."""
    cfg = _make_config(ci_auto=True, ci_max_cost=5.0)
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)
    engine._task_cost_tracking["T0001"] = 10.0  # over limit

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    mock_pr_manager.close_pr.assert_called_once()
    adapter = mock_spawner.running["T0001"]["adapter"]
    adapter.send_feedback.assert_not_called()


def test_changes_requested(mock_pr_manager, mock_spawner):
    """Verify review comments sent to agent."""
    cfg = _make_config(changes_auto=True)
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    asyncio.run(engine.handle_event(
        "review-submitted", "T0001", 42, "owner/repo"
    ))

    mock_pr_manager.get_review_comments.assert_called_once()
    adapter = mock_spawner.running["T0001"]["adapter"]
    adapter.send_feedback.assert_called_once()


def test_approved_auto_merge(mock_pr_manager, mock_spawner):
    """Verify auto-merge when configured."""
    cfg = _make_config(approved_auto=True, approved_action="auto-merge")
    mock_pr_manager.get_ci_status.return_value = CIStatus(
        overall="passed", checks=[], failed_checks=[],
    )
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    mock_pr_manager.merge_pr.assert_called_once_with("owner/repo", 42)


def test_approved_notify(mock_pr_manager, mock_spawner):
    """Verify notification when auto=false."""
    cfg = _make_config(approved_auto=False, approved_action="notify")
    mock_pr_manager.get_ci_status.return_value = CIStatus(
        overall="passed", checks=[], failed_checks=[],
    )
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    mock_pr_manager.merge_pr.assert_not_called()


def test_disabled_reaction(mock_pr_manager, mock_spawner):
    """Verify no action when auto=false for ci-failed."""
    cfg = _make_config(ci_auto=False)
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    asyncio.run(engine.handle_event(
        "ci-completed", "T0001", 42, "owner/repo"
    ))

    adapter = mock_spawner.running["T0001"]["adapter"]
    adapter.send_feedback.assert_not_called()
    mock_pr_manager.close_pr.assert_not_called()


def test_unknown_event_type(mock_pr_manager, mock_spawner):
    """Verify graceful handling of unexpected events."""
    cfg = _make_config()
    engine = ReactionsEngine(cfg, mock_pr_manager, mock_spawner)

    # Should not raise
    asyncio.run(engine.handle_event(
        "unknown-event", "T0001", 42, "owner/repo"
    ))
