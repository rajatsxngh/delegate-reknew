"""Tests for reknew.orchestration.dependency — topological wave grouping."""

from graphlib import CycleError

import pytest

from reknew.orchestration.dependency import resolve_dependencies


def _task(tid, deps=None):
    return {"id": tid, "dependencies": deps or []}


def test_no_dependencies():
    """3 independent tasks -> 1 wave with all 3."""
    tasks = [_task("T0001"), _task("T0002"), _task("T0003")]
    waves = resolve_dependencies(tasks)
    assert len(waves) == 1
    ids = {t["id"] for t in waves[0]}
    assert ids == {"T0001", "T0002", "T0003"}


def test_linear_chain():
    """A->B->C -> 3 waves with 1 task each."""
    tasks = [
        _task("T0001"),
        _task("T0002", ["T0001"]),
        _task("T0003", ["T0002"]),
    ]
    waves = resolve_dependencies(tasks)
    assert len(waves) == 3
    assert waves[0][0]["id"] == "T0001"
    assert waves[1][0]["id"] == "T0002"
    assert waves[2][0]["id"] == "T0003"


def test_diamond():
    """A->B, A->C, B->D, C->D -> [[A], [B,C], [D]]."""
    tasks = [
        _task("T0001"),
        _task("T0002", ["T0001"]),
        _task("T0003", ["T0001"]),
        _task("T0004", ["T0002", "T0003"]),
    ]
    waves = resolve_dependencies(tasks)
    assert len(waves) == 3

    assert waves[0][0]["id"] == "T0001"

    wave2_ids = {t["id"] for t in waves[1]}
    assert wave2_ids == {"T0002", "T0003"}

    assert waves[2][0]["id"] == "T0004"


def test_circular():
    """A->B->A -> raises CycleError."""
    tasks = [
        _task("T0001", ["T0002"]),
        _task("T0002", ["T0001"]),
    ]
    with pytest.raises(CycleError):
        resolve_dependencies(tasks)


def test_empty():
    """[] -> []."""
    assert resolve_dependencies([]) == []


def test_single_task():
    """[T0001 no deps] -> [[T0001]]."""
    tasks = [_task("T0001")]
    waves = resolve_dependencies(tasks)
    assert len(waves) == 1
    assert waves[0][0]["id"] == "T0001"
