"""Group tasks into parallelizable waves using topological sort."""

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
    """
    if not tasks:
        return []

    task_map = {t["id"]: t for t in tasks}

    graph: dict[str, set[str]] = {}
    for task in tasks:
        graph[task["id"]] = set(task["dependencies"])

    sorter = TopologicalSorter(graph)
    sorter.prepare()

    waves: list[list[dict]] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())  # sort for deterministic order
        wave = [task_map[tid] for tid in ready]
        waves.append(wave)
        sorter.done(*ready)

    return waves
