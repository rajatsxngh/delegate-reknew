"""Tests for reknew.orchestration.task_state — state machine + serialization."""

import pytest

from reknew.orchestration.capacity_router import RoutingDecision
from reknew.orchestration.task_state import Task, TaskState, VALID_TRANSITIONS


def _make_task(**overrides) -> Task:
    defaults = dict(
        id="T0001",
        title="Test task",
        description="Do something",
        files=["a.py"],
        dependencies=[],
        complexity="medium",
        estimated_minutes=30,
    )
    defaults.update(overrides)
    return Task(**defaults)


# ── State transitions ─────────────────────────────────────────────────────

def test_valid_transition():
    t = _make_task()
    assert t.state == TaskState.TODO
    t.transition(TaskState.CAPACITY_ROUTED)
    assert t.state == TaskState.CAPACITY_ROUTED
    assert len(t.history) == 1
    assert t.history[0]["from"] == "todo"
    assert t.history[0]["to"] == "capacity_routed"


def test_invalid_transition():
    t = _make_task()
    with pytest.raises(ValueError, match="Invalid transition"):
        t.transition(TaskState.DONE)


def test_full_happy_path():
    t = _make_task()
    t.transition(TaskState.CAPACITY_ROUTED)
    t.transition(TaskState.QUEUED)
    t.transition(TaskState.IN_PROGRESS)
    t.transition(TaskState.IN_REVIEW)
    t.transition(TaskState.PR_CREATED)
    t.transition(TaskState.CI_RUNNING)
    t.transition(TaskState.APPROVED)
    t.transition(TaskState.MERGING)
    t.transition(TaskState.DONE)
    assert t.state == TaskState.DONE
    assert t.completed_at != ""
    assert len(t.history) == 9


def test_ci_failure_retry_path():
    t = _make_task()
    t.transition(TaskState.CAPACITY_ROUTED)
    t.transition(TaskState.QUEUED)
    t.transition(TaskState.IN_PROGRESS)
    t.transition(TaskState.IN_REVIEW)
    t.transition(TaskState.PR_CREATED)
    t.transition(TaskState.CI_RUNNING)
    t.transition(TaskState.CI_FAILED)
    t.transition(TaskState.IN_PROGRESS)  # retry
    assert t.state == TaskState.IN_PROGRESS


def test_escalation_path():
    t = _make_task()
    t.transition(TaskState.CAPACITY_ROUTED)
    t.transition(TaskState.ESCALATED)
    assert t.state == TaskState.ESCALATED
    t.transition(TaskState.TODO)  # human re-routes
    assert t.state == TaskState.TODO


def test_done_has_no_transitions():
    t = _make_task()
    t.transition(TaskState.CAPACITY_ROUTED)
    t.transition(TaskState.QUEUED)
    t.transition(TaskState.IN_PROGRESS)
    t.transition(TaskState.IN_REVIEW)
    t.transition(TaskState.PR_CREATED)
    t.transition(TaskState.CI_RUNNING)
    t.transition(TaskState.APPROVED)
    t.transition(TaskState.MERGING)
    t.transition(TaskState.DONE)
    with pytest.raises(ValueError):
        t.transition(TaskState.TODO)


# ── Serialization ─────────────────────────────────────────────────────────

def test_to_dict():
    t = _make_task()
    t.routing_decision = RoutingDecision(
        task_id="T0001",
        target="ai",
        matched_rule="default -> ai",
        confidence="default",
    )
    d = t.to_dict()
    assert d["id"] == "T0001"
    assert d["state"] == "todo"
    assert d["routing_decision"]["target"] == "ai"
    assert d["files"] == ["a.py"]


def test_from_dict_roundtrip():
    t = _make_task()
    t.routing_decision = RoutingDecision(
        task_id="T0001",
        target="human",
        matched_rule="label:critical -> human",
        confidence="exact",
    )
    t.transition(TaskState.CAPACITY_ROUTED)
    d = t.to_dict()
    t2 = Task.from_dict(d)
    assert t2.id == t.id
    assert t2.state == TaskState.CAPACITY_ROUTED
    assert t2.routing_decision.target == "human"
    assert len(t2.history) == 1


def test_from_dict_no_routing():
    d = {
        "id": "T0002",
        "title": "x",
        "description": "y",
        "files": [],
        "dependencies": [],
        "complexity": "low",
        "estimated_minutes": 15,
        "state": "todo",
    }
    t = Task.from_dict(d)
    assert t.id == "T0002"
    assert t.routing_decision is None


# ── VALID_TRANSITIONS completeness ────────────────────────────────────────

def test_all_states_have_transition_entry():
    for state in TaskState:
        assert state in VALID_TRANSITIONS, (
            f"{state} missing from VALID_TRANSITIONS"
        )
