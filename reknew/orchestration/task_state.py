"""Extended task state machine with capacity routing and PR lifecycle."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum

from reknew.orchestration.capacity_router import RoutingDecision


class TaskState(Enum):
    """Full lifecycle states for a ReKnew task."""

    TODO = "todo"
    CAPACITY_ROUTED = "capacity_routed"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    PR_CREATED = "pr_created"
    CI_RUNNING = "ci_running"
    CI_FAILED = "ci_failed"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"
    ESCALATED = "escalated"


VALID_TRANSITIONS: dict[TaskState, list[TaskState]] = {
    TaskState.TODO: [TaskState.CAPACITY_ROUTED],
    TaskState.CAPACITY_ROUTED: [TaskState.QUEUED, TaskState.ESCALATED],
    TaskState.QUEUED: [TaskState.IN_PROGRESS],
    TaskState.IN_PROGRESS: [TaskState.IN_REVIEW, TaskState.FAILED],
    TaskState.IN_REVIEW: [TaskState.PR_CREATED, TaskState.IN_PROGRESS],
    TaskState.PR_CREATED: [TaskState.CI_RUNNING],
    TaskState.CI_RUNNING: [TaskState.APPROVED, TaskState.CI_FAILED],
    TaskState.CI_FAILED: [
        TaskState.IN_PROGRESS, TaskState.FAILED, TaskState.ESCALATED,
    ],
    TaskState.CHANGES_REQUESTED: [
        TaskState.IN_PROGRESS, TaskState.ESCALATED,
    ],
    TaskState.APPROVED: [TaskState.MERGING],
    TaskState.MERGING: [TaskState.DONE, TaskState.FAILED],
    TaskState.DONE: [],
    TaskState.FAILED: [TaskState.TODO],
    TaskState.ESCALATED: [TaskState.TODO],
}


@dataclass
class Task:
    """Full-lifecycle task with capacity routing and PR metadata."""

    id: str
    title: str
    description: str
    files: list[str]
    dependencies: list[str]
    complexity: str
    estimated_minutes: int
    state: TaskState = TaskState.TODO
    assigned_to: str = ""
    agent_type: str = ""
    routing_decision: RoutingDecision | None = None
    worktree_path: str = ""
    branch: str = ""
    pr_number: int = 0
    pr_url: str = ""
    ci_status: str = ""
    retry_count: int = 0
    max_retries: int = 2
    total_cost: float = 0.0
    history: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""

    def transition(self, new_state: TaskState) -> None:
        """Move to new state with validation and history tracking.

        Args:
            new_state: target TaskState

        Raises:
            ValueError: if the transition is not allowed
        """
        allowed = VALID_TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self.state.value} -> "
                f"{new_state.value}. Allowed: "
                f"{[s.value for s in allowed]}"
            )
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "timestamp": now,
        })
        self.state = new_state
        self.updated_at = now
        if new_state == TaskState.DONE:
            self.completed_at = now

    def to_dict(self) -> dict:
        """Serialize to dict for API responses and storage."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "files": self.files,
            "dependencies": self.dependencies,
            "complexity": self.complexity,
            "estimated_minutes": self.estimated_minutes,
            "state": self.state.value,
            "assigned_to": self.assigned_to,
            "agent_type": self.agent_type,
            "routing_decision": {
                "task_id": self.routing_decision.task_id,
                "target": self.routing_decision.target,
                "matched_rule": self.routing_decision.matched_rule,
                "confidence": self.routing_decision.confidence,
            } if self.routing_decision else None,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "ci_status": self.ci_status,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "total_cost": self.total_cost,
            "history": self.history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        """Deserialize from dict.

        Args:
            data: dict produced by to_dict()

        Returns:
            Task instance
        """
        rd_data = data.get("routing_decision")
        routing_decision = None
        if rd_data:
            routing_decision = RoutingDecision(
                task_id=rd_data["task_id"],
                target=rd_data["target"],
                matched_rule=rd_data["matched_rule"],
                confidence=rd_data["confidence"],
            )

        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            files=data["files"],
            dependencies=data["dependencies"],
            complexity=data["complexity"],
            estimated_minutes=data["estimated_minutes"],
            state=TaskState(data.get("state", "todo")),
            assigned_to=data.get("assigned_to", ""),
            agent_type=data.get("agent_type", ""),
            routing_decision=routing_decision,
            worktree_path=data.get("worktree_path", ""),
            branch=data.get("branch", ""),
            pr_number=data.get("pr_number", 0),
            pr_url=data.get("pr_url", ""),
            ci_status=data.get("ci_status", ""),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 2),
            total_cost=data.get("total_cost", 0.0),
            history=data.get("history", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            completed_at=data.get("completed_at", ""),
        )
