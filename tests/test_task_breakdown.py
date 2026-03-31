"""Tests for reknew.orchestration.task_breakdown."""

import json
from unittest.mock import MagicMock, patch

import pytest

from reknew.orchestration.task_breakdown import (
    break_down_spec,
    detect_file_conflicts,
    validate_task_graph,
)


def _make_task(tid, files=None, deps=None, **kwargs):
    """Helper to build a valid task dict."""
    return {
        "id": tid,
        "title": kwargs.get("title", f"Task {tid}"),
        "description": kwargs.get("description", f"Implement {tid}"),
        "files": files or [],
        "dependencies": deps or [],
        "estimated_minutes": kwargs.get("estimated_minutes", 30),
        "complexity": kwargs.get("complexity", "medium"),
    }


# ── validate_task_graph ──────────────────────────────────────────────────

def test_validate_valid_graph():
    tasks = [
        _make_task("T0001", files=["a.py"], deps=[]),
        _make_task("T0002", files=["b.py"], deps=["T0001"]),
        _make_task("T0003", files=["c.py"], deps=["T0001"]),
    ]
    validate_task_graph(tasks)  # should not raise


def test_validate_circular_dependency():
    tasks = [
        _make_task("T0001", deps=["T0003"]),
        _make_task("T0002", deps=["T0001"]),
        _make_task("T0003", deps=["T0002"]),
    ]
    with pytest.raises(ValueError, match="[Cc]ircular"):
        validate_task_graph(tasks)


def test_validate_unknown_dependency():
    tasks = [
        _make_task("T0001", deps=["T9999"]),
    ]
    with pytest.raises(ValueError, match="unknown task T9999"):
        validate_task_graph(tasks)


def test_validate_duplicate_ids():
    tasks = [
        _make_task("T0001"),
        _make_task("T0001"),
    ]
    with pytest.raises(ValueError, match="Duplicate"):
        validate_task_graph(tasks)


def test_validate_missing_fields():
    tasks = [{"id": "T0001"}]
    with pytest.raises(ValueError, match="missing fields"):
        validate_task_graph(tasks)


# ── detect_file_conflicts ────────────────────────────────────────────────

def test_detect_file_conflicts():
    tasks = [
        _make_task("T0001", files=["src/main.py"]),
        _make_task("T0002", files=["src/main.py"]),
    ]
    conflicts = detect_file_conflicts(tasks)
    assert len(conflicts) == 1
    assert conflicts[0] == ("T0001", "T0002", "src/main.py")


def test_detect_no_conflicts():
    tasks = [
        _make_task("T0001", files=["a.py"]),
        _make_task("T0002", files=["b.py"]),
    ]
    conflicts = detect_file_conflicts(tasks)
    assert conflicts == []


def test_detect_conflict_with_dependency_is_ok():
    """Tasks that share files but have a dependency should not conflict."""
    tasks = [
        _make_task("T0001", files=["src/main.py"]),
        _make_task("T0002", files=["src/main.py"], deps=["T0001"]),
    ]
    conflicts = detect_file_conflicts(tasks)
    assert conflicts == []


# ── break_down_spec (mocked) ─────────────────────────────────────────────

def test_break_down_spec_mock():
    sample_tasks = [
        _make_task("T0001", files=["config.py"]),
        _make_task("T0002", files=["main.py"], deps=["T0001"]),
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(sample_tasks))]

    with patch("reknew.orchestration.task_breakdown.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        result = break_down_spec("some spec")
        assert len(result) == 2
        assert result[0]["id"] == "T0001"
        assert result[1]["dependencies"] == ["T0001"]


def test_break_down_spec_invalid_json():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="This is not JSON at all")]

    with patch("reknew.orchestration.task_breakdown.anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = mock_response

        with pytest.raises(json.JSONDecodeError):
            break_down_spec("some spec")
