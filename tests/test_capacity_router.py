"""Tests for reknew.orchestration.capacity_router."""

import pytest

from reknew.orchestration.capacity_router import CapacityRouter, RoutingDecision


def _task(tid, files=None, complexity="medium", estimated_minutes=30):
    return {
        "id": tid,
        "title": f"Task {tid}",
        "description": f"Do {tid}",
        "files": files or ["a.py"],
        "dependencies": [],
        "estimated_minutes": estimated_minutes,
        "complexity": complexity,
    }


# ── Routing ───────────────────────────────────────────────────────────────

def test_label_match():
    router = CapacityRouter(["label:critical -> human", "default -> ai"])
    decision = router.route(_task("T0001"), issue_labels=["critical"])
    assert decision.target == "human"
    assert decision.confidence == "exact"
    assert "critical" in decision.matched_rule


def test_label_no_match():
    router = CapacityRouter(["label:critical -> human", "default -> ai"])
    decision = router.route(_task("T0001"), issue_labels=["bug"])
    assert decision.target == "ai"
    assert decision.confidence == "default"


def test_complexity_high():
    router = CapacityRouter(["complexity:high -> human", "default -> ai"])
    decision = router.route(_task("T0001", complexity="high"))
    assert decision.target == "human"
    assert decision.confidence == "exact"


def test_file_count_threshold():
    router = CapacityRouter(["file_count:>20 -> human", "default -> ai"])
    files = [f"file_{i}.py" for i in range(25)]
    decision = router.route(_task("T0001", files=files))
    assert decision.target == "human"
    assert decision.confidence == "exact"


def test_file_count_below_threshold():
    router = CapacityRouter(["file_count:>20 -> human", "default -> ai"])
    decision = router.route(_task("T0001", files=["a.py", "b.py"]))
    assert decision.target == "ai"


def test_estimated_minutes_threshold():
    router = CapacityRouter([
        "estimated_minutes:>60 -> human", "default -> ai",
    ])
    decision = router.route(_task("T0001", estimated_minutes=90))
    assert decision.target == "human"


def test_default_fallback():
    router = CapacityRouter(["label:security -> human", "default -> ai"])
    decision = router.route(_task("T0001"), issue_labels=[])
    assert decision.target == "ai"
    assert decision.confidence == "default"


def test_first_match_wins():
    router = CapacityRouter([
        "label:bug -> ai",
        "label:bug -> human",
        "default -> ai",
    ])
    decision = router.route(_task("T0001"), issue_labels=["bug"])
    assert decision.target == "ai"  # first rule wins


# ── Batch + Summary ──────────────────────────────────────────────────────

def test_route_batch():
    router = CapacityRouter([
        "label:critical -> human",
        "complexity:high -> human",
        "default -> ai",
    ])
    tasks = [
        _task("T0001", complexity="low"),
        _task("T0002", complexity="high"),
        _task("T0003", complexity="medium"),
        _task("T0004", complexity="low"),
        _task("T0005", complexity="medium"),
    ]
    decisions = router.route_batch(tasks, issue_labels=["critical"])
    # All tasks have "critical" label, so all go to human
    assert all(d.target == "human" for d in decisions.values())


def test_capacity_summary():
    router = CapacityRouter(["label:critical -> human", "default -> ai"])
    tasks = [
        _task("T0001"),
        _task("T0002"),
        _task("T0003"),
    ]
    decisions = router.route_batch(tasks, issue_labels=["bug"])
    summary = router.get_capacity_summary(decisions)
    assert summary["total"] == 3
    assert summary["ai"] == 3
    assert summary["human"] == 0
    assert len(summary["ai_tasks"]) == 3
    assert len(summary["human_tasks"]) == 0
    assert "default -> ai" in summary["rules_matched"]


# ── Validation ────────────────────────────────────────────────────────────

def test_invalid_rule_format():
    with pytest.raises(ValueError, match="Invalid rule format"):
        CapacityRouter(["badformat"])


def test_invalid_target():
    with pytest.raises(ValueError, match="Invalid rule format"):
        CapacityRouter(["label:x -> maybe"])


def test_empty_rules():
    with pytest.raises(ValueError, match="At least one rule required"):
        CapacityRouter([])


def test_no_default_rule(caplog):
    """Rules without a default -> warning logged but works."""
    import logging
    with caplog.at_level(logging.WARNING):
        router = CapacityRouter(["label:critical -> human"])
    assert "No default rule" in caplog.text
    # Should still route, falling through to catch-all
    decision = router.route(_task("T0001"), issue_labels=[])
    assert decision.target == "ai"  # internal fallback
    assert decision.confidence == "default"
