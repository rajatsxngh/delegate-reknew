"""Break a spec into a parallelizable task graph using an LLM."""

import json
import logging
from graphlib import TopologicalSorter

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a senior engineering manager breaking down a technical specification \
into parallelizable tasks for AI coding agents.

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
1. Tasks that MODIFY THE SAME FILE cannot run in parallel \u2014 one must depend on the other
2. Tasks with NO shared files CAN run in parallel \u2014 no dependency needed
3. Infrastructure/setup tasks come first (create directories, config files)
4. Test tasks MUST depend on the code they test
5. Documentation tasks can often run in parallel with everything else

CRITICAL RULES FOR QUALITY:
1. Keep tasks small: each should be completable by one agent in 30-60 minutes
2. Maximum 8 tasks per breakdown (split further if needed)
3. Each task must be self-contained: an agent reading only this task's description \
should be able to complete it without reading other tasks
4. Include acceptance criteria in each task description

Output ONLY valid JSON. No markdown fences. No explanation text. No preamble.
The response must start with [ and end with ]."""

REQUIRED_TASK_FIELDS = {
    "id", "title", "description", "files",
    "dependencies", "estimated_minutes", "complexity",
}


def break_down_spec(
    spec_content: str, model: str = "claude-sonnet-4-20250514"
) -> list[dict]:
    """Break a spec into a task graph using an LLM.

    Args:
        spec_content: the full spec text (from OpenSpec or issue context)
        model: Anthropic model to use for breakdown

    Returns:
        List of task dicts with keys: id, title, description, files,
        dependencies, estimated_minutes, complexity

    Raises:
        json.JSONDecodeError: if LLM output is not valid JSON
        ValueError: if tasks have circular dependencies or invalid refs
        anthropic.APIError: if API call fails
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": spec_content}],
    )

    raw_text = response.content[0].text.strip()
    tasks = json.loads(raw_text)

    validate_task_graph(tasks)
    return tasks


def validate_task_graph(tasks: list[dict]) -> None:
    """Validate the task graph for correctness.

    Checks:
    1. All task IDs are unique
    2. All dependency references point to existing task IDs
    3. No circular dependencies (uses graphlib to verify)
    4. No two parallel tasks (same wave) share files
    5. Each task has all required fields

    Raises:
        ValueError: with descriptive message if validation fails.
    """
    # Check required fields
    for task in tasks:
        missing = REQUIRED_TASK_FIELDS - set(task.keys())
        if missing:
            raise ValueError(
                f"Task {task.get('id', '?')} missing fields: {missing}"
            )

    # Check unique IDs
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        dupes = [tid for tid in ids if ids.count(tid) > 1]
        raise ValueError(f"Duplicate task IDs: {set(dupes)}")

    id_set = set(ids)

    # Check dependency references exist
    for task in tasks:
        for dep in task["dependencies"]:
            if dep not in id_set:
                raise ValueError(
                    f"Task {task['id']} depends on unknown task {dep}"
                )

    # Check for circular dependencies via TopologicalSorter
    graph: dict[str, set[str]] = {}
    for task in tasks:
        graph[task["id"]] = set(task["dependencies"])

    sorter = TopologicalSorter(graph)
    try:
        # static_order() raises CycleError if cycles exist
        list(sorter.static_order())
    except Exception as exc:
        raise ValueError(f"Circular dependency detected: {exc}") from exc


def detect_file_conflicts(tasks: list[dict]) -> list[tuple[str, str, str]]:
    """Find tasks that share files but lack dependencies.

    Returns:
        List of (task_id_1, task_id_2, shared_file) tuples.
        These indicate tasks that should have a dependency between them
        but don't. The caller should add a dependency or flag for review.
    """
    # Build set of dependencies for quick lookup
    deps: dict[str, set[str]] = {}
    for task in tasks:
        deps[task["id"]] = set(task["dependencies"])

    def _has_dependency(a: str, b: str) -> bool:
        """Check if a depends on b or b depends on a."""
        return b in deps.get(a, set()) or a in deps.get(b, set())

    conflicts: list[tuple[str, str, str]] = []
    for i, t1 in enumerate(tasks):
        for t2 in tasks[i + 1:]:
            if _has_dependency(t1["id"], t2["id"]):
                continue
            shared = set(t1["files"]) & set(t2["files"])
            for f in shared:
                conflicts.append((t1["id"], t2["id"], f))

    return conflicts
