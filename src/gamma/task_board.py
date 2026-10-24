from __future__ import annotations


def analyze_tasks(tasks):
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")

    ids = []
    seen_ids = set()
    statuses = {}
    deps = {}
    priorities = {}

    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("each task must be a dict")

        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task id must be a nonblank string")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task id: {task_id!r}")
        seen_ids.add(task_id)
        ids.append(task_id)

        status = task.get("status", "todo")
        if status not in ("todo", "running", "failed", "done"):
            raise ValueError(f"invalid status: {status!r}")
        statuses[task_id] = status

        raw_deps = task.get("depends_on", [])
        if raw_deps is None:
            raise ValueError("depends_on must be a list")
        if not isinstance(raw_deps, list):
            raise ValueError("depends_on must be a list")
        dep_list = []
        seen_deps = set()
        for dep in raw_deps:
            if not isinstance(dep, str) or not dep.strip():
                raise ValueError("dependency must be a nonblank string")
            if dep in seen_deps:
                raise ValueError(f"duplicate dependency: {dep!r}")
            seen_deps.add(dep)
            dep_list.append(dep)
        deps[task_id] = dep_list

        priority = task.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        priorities[task_id] = priority

    for task_id in ids:
        for dep in deps[task_id]:
            if dep == task_id:
                raise ValueError(f"self-dependency: {task_id!r}")
            if dep not in seen_ids:
                raise ValueError(f"missing dependency: {dep!r}")

    # Iterative cycle detection using Kahn's algorithm on the dependency graph.
    # Edge: dep -> task (task depends on dep). A cycle means some tasks never reach in-degree 0.
    in_degree = {task_id: len(deps[task_id]) for task_id in ids}
    dependents = {task_id: [] for task_id in ids}
    for task_id in ids:
        for dep in deps[task_id]:
            dependents[dep].append(task_id)

    queue = [task_id for task_id in ids if in_degree[task_id] == 0]
    processed = 0
    while queue:
        current = queue.pop()
        processed += 1
        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if processed != len(ids):
        raise ValueError("dependency cycle detected")

    ready = []
    waiting = {}
    running = []
    failed = []
    done = []

    for task_id in ids:
        status = statuses[task_id]
        if status == "running":
            running.append(task_id)
        elif status == "failed":
            failed.append(task_id)
        elif status == "done":
            done.append(task_id)
        else:  # todo
            unfinished = [dep for dep in deps[task_id] if statuses[dep] != "done"]
            if unfinished:
                waiting[task_id] = unfinished
            else:
                ready.append(task_id)

    # Sort ready by descending priority, then input order (stable sort preserves input order for ties).
    ready.sort(key=lambda task_id: -priorities[task_id])

    return {
        "ready": ready,
        "waiting": waiting,
        "running": running,
        "failed": failed,
        "done": done,
    }
