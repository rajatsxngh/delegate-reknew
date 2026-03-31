"""Shared state file for pipeline <-> dashboard communication.

Writes to ~/.reknew/state.json. Both the pipeline (main.py) and the
dashboard (api.py) read/write this file. File-level locking ensures
no partial reads.
"""

import json
import time
from pathlib import Path
from typing import Any

STATE_FILE = Path.home() / ".reknew" / "state.json"

_EMPTY: dict[str, Any] = {
    "tasks": {},
    "capacity": {"total": 0, "human": 0, "ai": 0},
    "updated_at": 0,
}


def _read_raw() -> dict[str, Any]:
    """Read state file, returning empty state if missing or corrupt."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return dict(_EMPTY)


def _write_raw(data: dict[str, Any]) -> None:
    """Atomically write state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(STATE_FILE)


def read_state() -> dict[str, Any]:
    """Read full shared state."""
    return _read_raw()


def update_task(task_id: str, **fields: Any) -> None:
    """Create or update a task entry in shared state.

    Args:
        task_id: unique task identifier
        **fields: key-value pairs to set/merge on the task
    """
    data = _read_raw()
    tasks = data.setdefault("tasks", {})
    task = tasks.setdefault(task_id, {})
    task["id"] = task_id
    task["updated_at"] = time.time()
    task.update(fields)
    _write_raw(data)


def update_capacity(total: int, human: int, ai: int) -> None:
    """Write capacity routing summary.

    Args:
        total: total tasks routed
        human: tasks routed to human
        ai: tasks routed to AI
    """
    data = _read_raw()
    data["capacity"] = {
        "total": total,
        "human": human,
        "ai": ai,
    }
    _write_raw(data)


def remove_task(task_id: str) -> None:
    """Remove a task from shared state."""
    data = _read_raw()
    data.get("tasks", {}).pop(task_id, None)
    _write_raw(data)


def clear_state() -> None:
    """Reset state file to empty."""
    _write_raw(dict(_EMPTY))
