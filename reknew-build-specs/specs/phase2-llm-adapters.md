# Phase 2: LLM Adapter Layer + Task Breakdown + Parallel Agents

## Goal
Refactor Delegate's Claude-only spawner into an adapter interface. Build the task breakdown engine that splits a spec into parallelizable tasks. Run multiple agents simultaneously on different tasks from the same issue.

## Prerequisites
- Phase 1 complete (issue connector and OpenSpec bridge working)
- Delegate fork running locally (`uv run delegate start` works)

## Files to create/modify (in order)

### 1. reknew/adapters/__init__.py
Empty file.

### 2. reknew/adapters/base_adapter.py

**Purpose:** Abstract interface that all LLM adapters implement. This is the contract between the orchestration core and any coding agent.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

class AgentStatus(Enum):
    RUNNING = "running"
    DONE = "done"
    STUCK = "stuck"           # no output for stuck_timeout seconds
    CRASHED = "crashed"       # process exited with non-zero code
    TIMEOUT = "timeout"       # exceeded agent_timeout

@dataclass
class FileChange:
    path: str
    change_type: str          # "added", "modified", "deleted"
    diff: str                 # git diff output for this file

class AgentAdapter(ABC):
    """Standard interface for all coding agents.

    The orchestration core calls these 4 methods without knowing
    which LLM is behind the adapter. Adding a new LLM = implementing
    these 4 methods in a new file.

    The handle returned by spawn() is opaque — it can be a PID,
    a session ID, an API response object, whatever the adapter needs.
    """

    @abstractmethod
    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> Any:
        """Start the agent in the given worktree.

        Args:
            worktree_path: absolute path to the git worktree
            prompt: the full task prompt (may be very long)
            task_id: unique task identifier (e.g., "T0114")

        Returns:
            Opaque handle used by poll_status and send_feedback

        Raises:
            FileNotFoundError: if worktree_path doesn't exist
            RuntimeError: if agent binary not found
        """

    @abstractmethod
    def poll_status(self, handle: Any) -> AgentStatus:
        """Check if the agent is done, stuck, or crashed.

        Called repeatedly by the monitor's asyncio polling loop
        (every poll_interval seconds, default 10).

        Returns:
            AgentStatus enum value
        """

    @abstractmethod
    def send_feedback(self, handle: Any, feedback: str) -> Any:
        """Send CI errors or review comments back to the agent.

        For subprocess-based agents: kills the current process and
        re-spawns with the feedback as the new prompt.

        For API-based agents: sends a follow-up message to the session.

        Args:
            handle: the handle from spawn()
            feedback: formatted string with error logs or review comments

        Returns:
            New handle (may be different if process was restarted)
        """

    def get_changes(self, worktree_path: str) -> list[FileChange]:
        """Collect changed files after the agent completes.

        Default implementation uses git diff. Most adapters can
        use this default since all agents commit to git.

        Args:
            worktree_path: absolute path to the git worktree

        Returns:
            List of FileChange objects with path, type, and diff
        """
        # DEFAULT IMPLEMENTATION — subclasses can override
        import subprocess

        # Get list of changed files
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD~1"],
            cwd=worktree_path,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Try comparing against the base branch instead
            result = subprocess.run(
                ["git", "diff", "--name-status", "main"],
                cwd=worktree_path,
                capture_output=True, text=True,
            )

        changes = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status_code = parts[0].strip()
            file_path = parts[1].strip()

            change_type = {
                "A": "added", "M": "modified", "D": "deleted",
                "R": "renamed", "C": "copied"
            }.get(status_code[0], "modified")

            # Get the actual diff for this file
            diff_result = subprocess.run(
                ["git", "diff", "HEAD~1", "--", file_path],
                cwd=worktree_path,
                capture_output=True, text=True,
            )

            changes.append(FileChange(
                path=file_path,
                change_type=change_type,
                diff=diff_result.stdout,
            ))

        return changes
```

### 3. reknew/adapters/claude_adapter.py

**Purpose:** Adapter for Claude Code CLI. This is a refactored version of what Phase 1's main.py does inline.

```python
import subprocess
import os
import time
from pathlib import Path
from typing import Any
from .base_adapter import AgentAdapter, AgentStatus

class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code CLI.

    Spawns Claude Code as a subprocess with --print flag (non-interactive).
    Monitors by checking process status and log file growth.
    Sends feedback by killing and re-spawning with new prompt.
    """

    def __init__(self, stuck_timeout: int = 300):
        """
        Args:
            stuck_timeout: seconds with no log output = stuck (default 300 = 5 min)
        """
        self.stuck_timeout = stuck_timeout
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, str] = {}
        self._last_activity: dict[str, float] = {}
        self._last_log_size: dict[str, int] = {}
        self._worktree_paths: dict[str, str] = {}

    def spawn(self, worktree_path: str, prompt: str, task_id: str) -> str:
        """Spawn Claude Code in the worktree.

        Uses: claude --print --dangerously-skip-permissions "{prompt}"

        Log output goes to: ~/.reknew/logs/{task_id}.log

        Returns: task_id as the handle
        """

    def poll_status(self, handle: str) -> AgentStatus:
        """Check Claude Code process status.

        Logic:
        1. If process.poll() is not None:
           - exit code 0 → DONE
           - exit code != 0 → CRASHED
        2. If process still running:
           - Check log file size vs last check
           - If size increased → update last_activity, return RUNNING
           - If size unchanged and (now - last_activity) > stuck_timeout → STUCK
           - Otherwise → RUNNING
        """

    def send_feedback(self, handle: str, feedback: str) -> str:
        """Kill current process, re-spawn with feedback as prompt.

        The feedback string contains CI error logs or review comments.
        The new prompt includes the original context plus the feedback.
        """

    def _ensure_claude_installed(self) -> None:
        """Check that 'claude' CLI is available.

        Raises RuntimeError with install instructions if not found.
        """
```

**Key implementation details:**
- `--print` flag makes Claude Code non-interactive (outputs to stdout, exits when done)
- `--dangerously-skip-permissions` allows autonomous execution (no confirmation prompts)
- Log file grows as the agent works — monitoring file size detects stuck agents
- Re-spawning for feedback: kill old process, start new one in same worktree with feedback prompt
- ANTHROPIC_API_KEY must be in environment

### 4. reknew/adapters/codex_adapter.py (stub for Phase 2, implement in Phase 3)

```python
class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex API.

    Unlike Claude Code (subprocess), Codex is API-based.
    spawn() makes an API call. poll_status() checks the API.
    """
    # TODO: Implement when testing Codex
    pass
```

### 5. reknew/adapters/gemini_adapter.py (stub)

```python
class GeminiAdapter(AgentAdapter):
    """Adapter for Gemini CLI.

    Similar to Claude Code — subprocess-based.
    """
    # TODO: Implement when testing Gemini
    pass
```

---

### 6. reknew/orchestration/__init__.py
Empty file.

### 7. reknew/orchestration/task_breakdown.py

**Purpose:** Use an LLM to break a spec into a JSON task graph with dependencies.

```python
SYSTEM_PROMPT = """You are a senior engineering manager breaking down a technical specification into parallelizable tasks for AI coding agents.

Given a specification, produce a JSON array of tasks. Each task must have:
- id: string (T0001, T0002, etc.)
- title: short description (max 10 words)
- description: detailed implementation instructions including:
  - What to create/modify
  - Expected behavior
  - Error handling requirements
  - Any relevant code patterns from the existing codebase
- files: list of file paths this task will CREATE or MODIFY
  - Be specific: "src/models/user.py" not "src/models/"
- dependencies: list of task IDs that must complete BEFORE this task can start
  - Empty list [] means this task has no dependencies and can start immediately
- estimated_minutes: rough time estimate (15-60 min range)
- complexity: "low", "medium", or "high"

CRITICAL RULES FOR DEPENDENCIES:
1. Tasks that MODIFY THE SAME FILE cannot run in parallel — one must depend on the other
2. Tasks with NO shared files CAN run in parallel — no dependency needed
3. Infrastructure/setup tasks come first (create directories, config files)
4. Test tasks MUST depend on the code they test
5. Documentation tasks can often run in parallel with everything else

CRITICAL RULES FOR QUALITY:
1. Keep tasks small: each should be completable by one agent in 30-60 minutes
2. Maximum 8 tasks per breakdown (split further if needed)
3. Each task must be self-contained: an agent reading only this task's description
   should be able to complete it without reading other tasks
4. Include acceptance criteria in each task description

Output ONLY valid JSON. No markdown fences. No explanation text. No preamble.
The response must start with [ and end with ]."""


def break_down_spec(spec_content: str, model: str = "claude-sonnet-4-20250514") -> list[dict]:
    """Break a spec into a task graph using an LLM.

    Args:
        spec_content: the full spec text (from OpenSpec or issue context)
        model: Anthropic model to use for breakdown

    Returns:
        List of task dicts with keys: id, title, description, files,
        dependencies, estimated_minutes, complexity

    Raises:
        json.JSONDecodeError: if LLM output is not valid JSON
        ValueError: if tasks have circular dependencies
        ValueError: if tasks reference unknown dependency IDs
        anthropic.APIError: if API call fails
    """

def validate_task_graph(tasks: list[dict]) -> None:
    """Validate the task graph for correctness.

    Checks:
    1. All task IDs are unique
    2. All dependency references point to existing task IDs
    3. No circular dependencies (use graphlib to verify)
    4. No two parallel tasks (same wave) share files
    5. Each task has all required fields

    Raises ValueError with descriptive message if validation fails.
    """

def detect_file_conflicts(tasks: list[dict]) -> list[tuple[str, str, str]]:
    """Find tasks that share files but lack dependencies.

    Returns:
        List of (task_id_1, task_id_2, shared_file) tuples.
        These indicate tasks that should have a dependency between them
        but don't. The caller should add a dependency or flag for review.
    """
```

**Test file: tests/test_task_breakdown.py**

Test cases:
- `test_validate_valid_graph`: 3 tasks with valid deps, should pass
- `test_validate_circular_dependency`: A→B→C→A should raise ValueError
- `test_validate_unknown_dependency`: T0001 depends on T9999, should raise
- `test_validate_duplicate_ids`: Two tasks with same ID, should raise
- `test_detect_file_conflicts`: Two tasks both modify "src/main.py" with no dep
- `test_detect_no_conflicts`: Two tasks with separate files, should return []
- `test_break_down_spec_mock`: Mock the Anthropic API call, verify JSON parsing
- `test_break_down_spec_invalid_json`: Mock API returning prose, verify JSONDecodeError

---

### 8. reknew/orchestration/dependency.py

**Purpose:** Group tasks into parallelizable waves using topological sort.

```python
from graphlib import TopologicalSorter

def resolve_dependencies(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into waves of parallel execution.

    Wave 1: tasks with no dependencies (all can run at once)
    Wave 2: tasks depending only on Wave 1 (all can run at once)
    Wave N: tasks depending on Wave N-1

    Args:
        tasks: list of task dicts (must have "id" and "dependencies" keys)

    Returns:
        List of waves, where each wave is a list of task dicts.
        Tasks within the same wave can safely run in parallel.

    Raises:
        graphlib.CycleError: if circular dependency detected

    Example:
        Input: [
            {"id": "T0001", "dependencies": []},
            {"id": "T0002", "dependencies": ["T0001"]},
            {"id": "T0003", "dependencies": []},
            {"id": "T0004", "dependencies": ["T0002", "T0003"]}
        ]
        Output: [
            [task_T0001, task_T0003],   # Wave 1: parallel
            [task_T0002],                # Wave 2: needs T0001
            [task_T0004],                # Wave 3: needs T0002 + T0003
        ]
    """
```

**Test file: tests/test_dependency.py**

Test cases:
- `test_no_dependencies`: 3 independent tasks → 1 wave with all 3
- `test_linear_chain`: A→B→C → 3 waves with 1 task each
- `test_diamond`: A→B, A→C, B→D, C→D → [[A], [B,C], [D]]
- `test_circular`: A→B→A → raises CycleError
- `test_empty`: [] → []
- `test_single_task`: [T0001 no deps] → [[T0001]]

---

### 9. reknew/agents/__init__.py
Empty file.

### 10. reknew/agents/spawner.py

**Purpose:** High-level agent spawning that uses the adapter interface and integrates with Delegate's worktree manager.

```python
class AgentSpawner:
    """Spawns agents using the configured adapter.

    Integrates with Delegate's worktree manager for isolation.
    Tracks all running agents and their handles.
    """

    def __init__(self, config: ReknewConfig):
        self.config = config
        self.adapter = self._load_adapter(config.defaults.agent)
        self.running: dict[str, dict] = {}  # task_id → {handle, worktree, adapter}

    def _load_adapter(self, agent_name: str) -> AgentAdapter:
        """Load the adapter for the configured agent.

        Mapping:
        - "claude-code" → ClaudeCodeAdapter
        - "codex" → CodexAdapter
        - "gemini" → GeminiAdapter
        - etc.

        Raises ValueError for unknown agent names.
        """

    def spawn_task(self, task_id: str, worktree_path: str, prompt: str) -> None:
        """Spawn an agent for a task.

        1. Use the adapter to spawn the agent
        2. Store the handle in self.running
        3. Log the spawn event
        """

    def spawn_wave(self, tasks: list[dict], worktree_manager) -> None:
        """Spawn agents for all tasks in a wave (parallel).

        For each task:
        1. Create a worktree via worktree_manager
        2. Set up the environment (install deps)
        3. Spawn the agent in the worktree
        """

    def get_status(self, task_id: str) -> AgentStatus:
        """Get status of a running agent."""

    def get_all_statuses(self) -> dict[str, AgentStatus]:
        """Get status of all running agents."""
```

---

### 11. reknew/agents/monitor.py

**Purpose:** Async polling loop that watches all running agents.

```python
class AgentMonitor:
    """Monitors all running agents via asyncio polling.

    Pattern from Delegate: agents are stateless between turns.
    The monitor polls adapters for status and fires callbacks
    when agents complete, get stuck, or crash.
    """

    def __init__(self, poll_interval: int = 10):
        self.poll_interval = poll_interval
        self._agents: dict[str, MonitoredAgent] = {}
        self._callbacks: dict[str, Callable] = {}

    def register(self, task_id: str, handle: Any, adapter: AgentAdapter,
                 worktree_path: str, max_runtime: int = 3600) -> None:
        """Register an agent for monitoring."""

    def on(self, event: str, callback: Callable) -> None:
        """Register callback for events: 'done', 'stuck', 'crashed', 'timeout'"""

    async def run(self) -> None:
        """Main monitoring loop. Runs until all agents finish.

        Every poll_interval seconds:
        1. For each registered agent:
           a. Check if exceeded max_runtime → TIMEOUT
           b. Call adapter.poll_status(handle)
           c. If status changed → fire callback
           d. If DONE/STUCK/CRASHED/TIMEOUT → remove from monitoring
        2. If no agents left → return
        """

    def get_summary(self) -> dict[str, dict]:
        """Get current status of all monitored agents.

        Returns: {task_id: {status, elapsed_seconds, worktree_path}}
        """
```

---

## Updated reknew/main.py for Phase 2

Extend main.py with a new command:

```python
async def run_issue_parallel(repo_name: str, issue_number: int) -> None:
    """Process an issue with parallel agents.

    Steps:
    1-6: Same as Phase 1 (fetch issue, generate spec)
    7. Break spec into task graph via task_breakdown
    8. Resolve dependencies into waves
    9. For each wave:
       a. Create worktrees for all tasks in the wave
       b. Spawn agents in parallel
       c. Monitor until all agents in the wave complete
       d. Collect results
    10. Print summary: tasks completed, failed, changes produced
    """
```

## Definition of done

- [ ] `base_adapter.py` defines the 4-method interface with AgentStatus enum
- [ ] `claude_adapter.py` implements the interface (refactored from Phase 1 inline code)
- [ ] `task_breakdown.py` calls LLM and produces validated task graphs
- [ ] `dependency.py` groups tasks into parallel waves
- [ ] `spawner.py` loads adapters and spawns agents
- [ ] `monitor.py` polls agents and fires callbacks
- [ ] `main.py` updated with `run_issue_parallel` that processes waves
- [ ] All tests pass
- [ ] Successfully processed an issue with 2+ parallel agents
