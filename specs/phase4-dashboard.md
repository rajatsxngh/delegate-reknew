# Phase 4: Capacity Router + Dashboard + Executive View

## Goal
Build the capacity management layer (Les's whiteboard concept) and the executive dashboard. This is the ReKnew IP — the thing that makes this a product, not just a developer tool.

## Prerequisites
- Phase 3 complete (full autonomous pipeline with GitHub integration)
- Delegate's web UI running (`uv run delegate start` opens the dashboard)

## Files to create (in order)

### 1. reknew/orchestration/capacity_router.py

**Purpose:** Route tasks to human teams or AI agents based on configurable rules.

```python
@dataclass
class RoutingDecision:
    task_id: str
    target: str                    # "human" or "ai"
    matched_rule: str              # the rule string that matched
    confidence: str                # "exact" (label match) or "default"

class CapacityRouter:
    """Routes tasks to human or AI based on YAML rules.

    This is the core ReKnew IP. No competing tool offers this.

    Rules are evaluated in order. First match wins.
    Supported conditions:
    - label:{value} — matches if task has a GitHub label with this value
    - complexity:{low|medium|high} — matches task complexity from breakdown
    - priority:{p0|p1|p2|p3} — matches issue priority label
    - file_count:>{N} — matches if task touches more than N files
    - estimated_minutes:>{N} — matches if estimated time exceeds N
    - default — always matches (catch-all, should be last)

    Target must be "human" or "ai".
    """

    def __init__(self, rules: list[str]):
        """Parse rule strings into structured rules.

        Args:
            rules: list from config, e.g., ["label:critical -> human", "default -> ai"]

        Raises:
            ValueError: if any rule has invalid format
        """
        self.rules = self._parse_rules(rules)

    def _parse_rules(self, raw_rules: list[str]) -> list[dict]:
        """Parse "condition -> target" strings.

        Each rule becomes:
        {
            "type": "match" | "default",
            "field": "label" | "complexity" | "priority" | "file_count" | "estimated_minutes",
            "operator": "eq" | "gt",  # eq for exact match, gt for >N
            "value": str | int,
            "target": "human" | "ai",
            "raw": "label:critical -> human"
        }
        """

    def route(self, task: dict, issue_labels: list[str] | None = None) -> RoutingDecision:
        """Determine if a task goes to human or AI.

        Args:
            task: task dict from task_breakdown (has id, files, complexity, estimated_minutes)
            issue_labels: labels from the original GitHub issue (optional)

        Returns:
            RoutingDecision with target, matched rule, and confidence

        The task dict is checked against each rule in order:
        - "label:" rules check issue_labels
        - "complexity:" rules check task["complexity"]
        - "file_count:" rules check len(task["files"])
        - "estimated_minutes:" rules check task["estimated_minutes"]
        - "default" always matches
        """

    def route_batch(self, tasks: list[dict], issue_labels: list[str] | None = None
                    ) -> dict[str, RoutingDecision]:
        """Route all tasks from a breakdown.

        Returns: {task_id: RoutingDecision}
        """

    def get_capacity_summary(self, decisions: dict[str, RoutingDecision]) -> dict:
        """Summarize routing decisions.

        Returns:
        {
            "total": 5,
            "human": 2,
            "ai": 3,
            "human_tasks": ["T0001", "T0004"],
            "ai_tasks": ["T0002", "T0003", "T0005"],
            "rules_matched": {"label:critical -> human": 2, "default -> ai": 3}
        }
        """
```

**Test file: tests/test_capacity_router.py**

Test cases:
- `test_label_match`: Task with label "critical" → routes to human
- `test_label_no_match`: Task with label "bug" (no rule for it) → falls to default
- `test_complexity_high`: Task with complexity "high" → routes to human
- `test_file_count_threshold`: Task with 25 files (rule: file_count:>20) → human
- `test_default_fallback`: No matching rules → uses default rule
- `test_first_match_wins`: Multiple matching rules → first one used
- `test_route_batch`: 5 tasks → correct split
- `test_capacity_summary`: Verify summary counts
- `test_invalid_rule_format`: "badformat" → ValueError
- `test_invalid_target`: "label:x -> maybe" → ValueError
- `test_empty_rules`: No rules → ValueError("At least one rule required")
- `test_no_default_rule`: Rules without a default → warning logged but works

---

### 2. reknew/orchestration/task_state.py

**Purpose:** Extended task state machine with capacity routing state.

```python
class TaskState(Enum):
    TODO = "todo"
    CAPACITY_ROUTED = "capacity_routed"     # NEW: assigned to human or AI
    QUEUED = "queued"                        # NEW: waiting for available agent
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    PR_CREATED = "pr_created"               # NEW: PR opened on GitHub
    CI_RUNNING = "ci_running"               # NEW: CI in progress
    CI_FAILED = "ci_failed"                 # NEW: CI failed, retrying
    CHANGES_REQUESTED = "changes_requested" # NEW: reviewer requested changes
    APPROVED = "approved"                   # NEW: PR approved
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"                       # exhausted retries or crashed
    ESCALATED = "escalated"                 # NEW: sent to human after AI failure

VALID_TRANSITIONS = {
    TaskState.TODO: [TaskState.CAPACITY_ROUTED],
    TaskState.CAPACITY_ROUTED: [TaskState.QUEUED, TaskState.ESCALATED],
    TaskState.QUEUED: [TaskState.IN_PROGRESS],
    TaskState.IN_PROGRESS: [TaskState.IN_REVIEW, TaskState.FAILED],
    TaskState.IN_REVIEW: [TaskState.PR_CREATED, TaskState.IN_PROGRESS],
    TaskState.PR_CREATED: [TaskState.CI_RUNNING],
    TaskState.CI_RUNNING: [TaskState.APPROVED, TaskState.CI_FAILED],
    TaskState.CI_FAILED: [TaskState.IN_PROGRESS, TaskState.FAILED, TaskState.ESCALATED],
    TaskState.CHANGES_REQUESTED: [TaskState.IN_PROGRESS, TaskState.ESCALATED],
    TaskState.APPROVED: [TaskState.MERGING],
    TaskState.MERGING: [TaskState.DONE, TaskState.FAILED],
    TaskState.DONE: [],
    TaskState.FAILED: [TaskState.TODO],        # can retry
    TaskState.ESCALATED: [TaskState.TODO],     # human can re-route to AI
}

@dataclass
class Task:
    id: str
    title: str
    description: str
    files: list[str]
    dependencies: list[str]
    complexity: str
    estimated_minutes: int
    state: TaskState = TaskState.TODO
    assigned_to: str = ""                    # "ai" or "human:{name}"
    agent_type: str = ""                     # "claude-code", "codex", etc.
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
        """Move to new state with validation and history tracking."""

    def to_dict(self) -> dict:
        """Serialize to dict for API responses and storage."""

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Deserialize from dict."""
```

---

### 3. reknew/dashboard/__init__.py
Empty file.

### 4. reknew/dashboard/api.py

**Purpose:** FastAPI routes for the ReKnew dashboard.

```python
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

def create_app(config: ReknewConfig, daemon) -> FastAPI:
    """Create the FastAPI app with all routes.

    Routes:

    GET /api/health
        → {"status": "ok", "version": "0.1.0", "uptime_seconds": int}

    GET /api/config
        → serialized ReknewConfig (redacted secrets)

    GET /api/projects
        → list of projects with issue counts

    GET /api/projects/{name}/issues
        → list of issues for a project (from GitHub API)

    POST /api/projects/{name}/issues/{number}/process
        → trigger processing of an issue through the pipeline
        → returns {task_id, status: "processing"}

    GET /api/tasks
        → list of all tasks with current state
        → supports query params: ?state=in_progress&project=slayer

    GET /api/tasks/{task_id}
        → detailed task info including history, PR link, CI status

    GET /api/capacity
        → capacity management summary:
        {
            "total_issues": 89,
            "allocated_human": 12,
            "allocated_ai": 34,
            "unallocated": 43,
            "ai_completed_today": 8,
            "avg_time_to_merge": "32m",
            "compression_ratio": "2 weeks → 30 minutes"
        }

    GET /api/capacity/history
        → historical capacity data for charts
        [{date, human_tasks, ai_tasks, completed, backlog_size}]

    GET /api/agents
        → list of all running agents with status
        [{task_id, agent_type, status, elapsed, worktree}]

    GET /api/prs
        → list of all open PRs from AI agents
        [{pr_number, title, ci_status, review_status, task_id}]

    WebSocket /ws/events
        → real-time event stream:
        {type: "task_update", data: {task_id, old_state, new_state}}
        {type: "agent_status", data: {task_id, status}}
        {type: "ci_result", data: {task_id, pr_number, status}}
        {type: "capacity_update", data: {human, ai, completed}}
    """
```

### 5. reknew/dashboard/websocket.py

**Purpose:** WebSocket/SSE manager for real-time updates.

```python
class EventBroadcaster:
    """Manages WebSocket connections and broadcasts events.

    Components emit events:
    - Agent monitor emits "agent_status" when an agent finishes
    - Reactions engine emits "ci_result" when CI completes
    - Task state machine emits "task_update" on every transition
    - Capacity router emits "capacity_update" on routing decisions

    The broadcaster relays these to all connected WebSocket clients.
    """

    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection."""

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Send event to all connected clients.

        Message format: {"type": event_type, "data": data, "timestamp": iso_str}
        Silently drops connections that have closed.
        """

    async def emit(self, event_type: str, data: dict) -> None:
        """Alias for broadcast. Used by other components to emit events."""
```

---

### 6. reknew/daemon.py

**Purpose:** The central daemon that ties everything together. Extends Delegate's daemon.

```python
class ReknewDaemon:
    """Central orchestration daemon.

    This wraps Delegate's existing daemon and adds the ReKnew layer:
    - Issue ingestion (via connector)
    - Capacity routing (via router)
    - Reactions handling (via engine)
    - Dashboard serving (via FastAPI)

    The daemon runs as an asyncio event loop with multiple concurrent tasks:
    1. Delegate's existing turn dispatch loop
    2. GitHub webhook/poll listener
    3. Reactions engine event handler
    4. FastAPI server for dashboard
    """

    def __init__(self, config_path: str = "reknew.yaml"):
        self.config = load_config(config_path)

        # ReKnew components
        self.github_api = GitHubAPI()
        self.connector = GitHubConnector()
        self.pr_manager = PRManager(self.github_api, self.config)
        self.router = CapacityRouter(self.config.capacity.rules)
        self.spawner = AgentSpawner(self.config)
        self.monitor = AgentMonitor(self.config.defaults.stuck_timeout)
        self.reactions = ReactionsEngine(self.config, self.pr_manager, self.spawner)
        self.webhook = WebhookListener(self.config, self.pr_manager)
        self.broadcaster = EventBroadcaster()

        # Task storage
        self.tasks: dict[str, Task] = {}

        # Wire up event callbacks
        self._setup_callbacks()

    def _setup_callbacks(self) -> None:
        """Connect all event handlers.

        Monitor → agent done/stuck/crashed → create PR or handle failure
        Webhook → ci-completed/review-submitted → reactions engine
        Task transitions → broadcaster → dashboard
        """

    async def process_issue(self, project_name: str, issue_number: int) -> list[str]:
        """Process an issue through the full pipeline.

        Returns: list of task IDs created
        """

    async def run(self) -> None:
        """Start the daemon.

        Launches concurrent tasks:
        1. Delegate daemon (existing)
        2. Webhook/poll listener
        3. FastAPI server (dashboard)
        4. Agent monitor loop

        Runs until interrupted (Ctrl+C).
        """
```

---

## Dashboard UI (React)

The dashboard extends Delegate's existing frontend. Add new React components in `reknew/dashboard/frontend/`.

### Key views:

**1. Capacity overview (main page)**
- Large numbers: total demand, allocated (human), allocated (AI), unallocated
- Pie chart: human vs AI task split
- Line chart: backlog over time (shrinking as AI processes tasks)
- Headline metric: "2 weeks → 30 minutes" compression ratio

**2. Pipeline view**
- Kanban-style columns: todo → routed → in_progress → PR → CI → review → merged
- Cards move between columns in real-time via WebSocket
- Each card shows: task title, agent type, elapsed time

**3. Agent status view (extends Delegate's existing view)**
- List of all running agents
- Real-time log streaming
- Status indicators: running (green), stuck (yellow), crashed (red)

**4. PR pipeline view**
- All open PRs from AI agents
- CI status badge (green/yellow/red)
- Review status (approved/changes requested/pending)
- One-click merge button for approved PRs

**5. Settings page**
- View and edit reknew.yaml
- Test connection to GitHub
- View API rate limit status

### React component structure:
```
reknew/dashboard/frontend/
├── src/
│   ├── App.jsx
│   ├── components/
│   │   ├── CapacityOverview.jsx
│   │   ├── PipelineView.jsx
│   │   ├── AgentStatus.jsx
│   │   ├── PRPipeline.jsx
│   │   ├── BacklogChart.jsx
│   │   ├── TaskCard.jsx
│   │   └── MetricCard.jsx
│   ├── hooks/
│   │   ├── useWebSocket.js
│   │   └── useApi.js
│   └── utils/
│       └── formatters.js
├── package.json
└── index.html
```

Dependencies: React, Recharts (for charts), Tailwind CSS (for styling).

---

## CLI entry point

Update reknew/cli.py:

```python
import click

@click.group()
def main():
    """ReKnew AI-SDLC Platform"""

@main.command()
@click.option("--port", default=3000)
@click.option("--config", default="reknew.yaml")
def start(port, config):
    """Start the ReKnew daemon with dashboard."""

@main.command()
@click.argument("repo")
@click.argument("issue", type=int)
def process(repo, issue):
    """Process a single issue through the pipeline."""

@main.command()
def status():
    """Show status of all running agents and tasks."""

@main.command()
def config():
    """Validate and display current configuration."""
```

Usage:
```bash
reknew start                    # Start daemon + dashboard
reknew process owner/repo 114   # Process one issue
reknew status                   # Show running agents
reknew config                   # Validate config
```

---

## Definition of done

- [ ] `capacity_router.py` routes tasks based on YAML rules
- [ ] `task_state.py` tracks full lifecycle with new states (PR_CREATED, CI_RUNNING, etc.)
- [ ] `dashboard/api.py` serves all REST endpoints
- [ ] `dashboard/websocket.py` broadcasts real-time events
- [ ] `daemon.py` wires everything together
- [ ] `cli.py` provides start/process/status/config commands
- [ ] Dashboard shows capacity overview with charts
- [ ] Dashboard shows pipeline view with task cards
- [ ] Dashboard shows PR pipeline with CI status
- [ ] End-to-end demo on sp-enablers-slayer: issue → agent → PR → CI → dashboard shows it all
- [ ] All tests pass
