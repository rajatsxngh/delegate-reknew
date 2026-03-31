"""Entry point — processes GitHub issues via single agent or parallel agents."""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from reknew.config import load_config
from reknew.connectors.github_connector import GitHubConnector
from reknew.spec.openspec_bridge import OpenSpecBridge
from reknew.orchestration.task_breakdown import break_down_spec
from reknew.orchestration.dependency import resolve_dependencies
from reknew.agents.spawner import AgentSpawner
from reknew.agents.monitor import AgentMonitor
from reknew.adapters.base_adapter import AgentStatus
from reknew.github.api import GitHubAPI
from reknew.github.pr_manager import PRManager
from reknew.github.webhook_listener import WebhookListener
from reknew.reactions.engine import ReactionsEngine

logger = logging.getLogger(__name__)

REKNEW_HOME = Path.home() / ".reknew"
REPOS_DIR = REKNEW_HOME / "repos"
WORKTREES_DIR = REKNEW_HOME / "worktrees"
LOGS_DIR = REKNEW_HOME / "logs"


def _ensure_dirs() -> None:
    """Create ~/.reknew/ subdirectories if they don't exist."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _check_prerequisites() -> bool:
    """Check that required tools and env vars are available.

    Returns:
        True if all prerequisites met, False otherwise.
    """
    ok = True

    if not os.environ.get("GITHUB_TOKEN"):
        print("Error: GITHUB_TOKEN environment variable not set.")
        print("  export GITHUB_TOKEN=ghp_your_token")
        ok = False

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-your_key")
        ok = False

    if not shutil.which("claude"):
        print("Error: Claude Code CLI not found.")
        print("  Install: npm install -g @anthropic-ai/claude-code")
        ok = False

    if not shutil.which("openspec"):
        print("Warning: openspec CLI not found. Spec generation will be skipped.")

    return ok


def _clone_repo(repo_name: str, default_branch: str) -> Path:
    """Clone repo to ~/.reknew/repos/{repo-name}/ if not already cloned.

    Args:
        repo_name: "owner/repo" format
        default_branch: Branch to clone

    Returns:
        Path to the local clone.
    """
    repo_slug = repo_name.replace("/", "-")
    clone_path = REPOS_DIR / repo_slug

    if clone_path.exists():
        # Pull latest changes
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=clone_path,
            capture_output=True,
            text=True,
        )
        return clone_path

    subprocess.run(
        [
            "git", "clone",
            f"https://github.com/{repo_name}.git",
            str(clone_path),
            "--branch", default_branch,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return clone_path


def _create_worktree(
    clone_path: Path, issue_number: int
) -> tuple[Path, str]:
    """Create a git worktree for the issue.

    Args:
        clone_path: Path to the repo clone
        issue_number: GitHub issue number

    Returns:
        Tuple of (worktree_path, branch_name).
    """
    task_id = f"T{issue_number:04d}"
    branch_name = f"reknew/task/{task_id}"
    worktree_path = WORKTREES_DIR / task_id

    if worktree_path.exists():
        shutil.rmtree(worktree_path)

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=clone_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


def _spawn_agent(
    worktree_path: Path, prompt: str, issue_number: int
) -> subprocess.Popen:
    """Spawn Claude Code in the worktree with the agent prompt.

    Args:
        worktree_path: Working directory for the agent
        prompt: The agent's instruction prompt
        issue_number: For log file naming

    Returns:
        The running Popen process.
    """
    task_id = f"T{issue_number:04d}"
    log_path = LOGS_DIR / f"{task_id}.log"

    log_file = open(log_path, "w")
    process = subprocess.Popen(
        ["claude", "--print", "--dangerously-skip-permissions", prompt],
        cwd=worktree_path,
        stdout=log_file,
        stderr=log_file,
    )
    return process


async def run_single_issue(repo_name: str, issue_number: int) -> None:
    """Process one issue through the full Phase 1 pipeline.

    Steps:
    1. Load config from reknew.yaml
    2. Fetch issue via GitHubConnector
    3. Format for OpenSpec
    4. Clone repo if not already cloned
    5. Initialize OpenSpec in the clone
    6. Create change proposal
    7. Create a git worktree
    8. Copy the openspec change proposal into the worktree
    9. Spawn Claude Code in the worktree with the agent prompt
    10. Poll process every 5 seconds until done
    11. Print results
    """
    task_id = f"T{issue_number:04d}"

    # Step 1: Load config
    cfg = load_config()

    # Step 2: Fetch issue
    print(f"[1/5] Fetching issue #{issue_number} from {repo_name}...")
    connector = GitHubConnector()
    ctx = connector.fetch_issue(repo_name, issue_number)
    print(f"      Title: {ctx.title}")
    print(f"      Labels: {', '.join(ctx.labels) or 'none'}")
    print(f"      Files in repo: {len(ctx.file_tree)}")

    # Step 3: Format for OpenSpec
    print("[2/5] Generating spec via OpenSpec...")
    openspec_text = connector.format_for_openspec(ctx)

    # Step 4: Clone repo
    project_cfg = None
    for proj in cfg.projects.values():
        if proj.repo == repo_name:
            project_cfg = proj
            break
    default_branch = project_cfg.default_branch if project_cfg else "main"

    clone_path = _clone_repo(repo_name, default_branch)

    # Step 5: Initialize OpenSpec
    bridge = OpenSpecBridge(str(clone_path))
    try:
        bridge.ensure_initialized()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("      (openspec init skipped)")

    # Step 6: Create change proposal
    change_name = f"issue-{issue_number}"
    bridge.create_change_proposal(openspec_text, change_name)
    prompt = bridge.get_agent_prompt(change_name)

    # Step 7: Create worktree
    print("[3/5] Creating isolated worktree...")
    worktree_path, branch_name = _create_worktree(clone_path, issue_number)
    print(f"      Worktree: {worktree_path}")
    print(f"      Branch: {branch_name}")

    # Step 8: Copy openspec proposal into worktree
    src_openspec = clone_path / "openspec"
    dst_openspec = worktree_path / "openspec"
    if src_openspec.exists():
        shutil.copytree(src_openspec, dst_openspec, dirs_exist_ok=True)

    # Step 9: Spawn agent
    print("[4/5] Spawning Claude Code agent...")
    process = _spawn_agent(worktree_path, prompt, issue_number)

    # Step 10: Poll
    log_path = LOGS_DIR / f"{task_id}.log"
    print(f"[5/5] Agent working... (logs: {log_path})")
    while process.poll() is None:
        print("      Still working...")
        await asyncio.sleep(5)

    # Step 11: Report results
    rc = process.returncode
    if rc == 0:
        print(f"\nAgent completed successfully!")
        print(f"  Check changes: cd {worktree_path} && git diff")
        print(f"  View logs: cat {log_path}")
    else:
        print(f"\nAgent failed (exit code {rc}).")
        print(f"  View logs: cat {log_path}")
        # Print last 20 lines of log
        try:
            lines = log_path.read_text().splitlines()
            tail = lines[-20:] if len(lines) > 20 else lines
            print("  --- Last lines of log ---")
            for line in tail:
                print(f"  {line}")
        except Exception:
            pass


def _create_task_worktree(
    clone_path: Path, task_id: str
) -> tuple[Path, str]:
    """Create a git worktree for a sub-task.

    Args:
        clone_path: Path to the repo clone
        task_id: Task identifier (e.g. "T0001")

    Returns:
        Tuple of (worktree_path, branch_name).
    """
    branch_name = f"reknew/task/{task_id}"
    worktree_path = WORKTREES_DIR / task_id

    if worktree_path.exists():
        shutil.rmtree(worktree_path)

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=clone_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


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
    10. Print summary
    """
    # Steps 1-3: Load config, fetch issue, format spec
    cfg = load_config()

    print(f"[1/7] Fetching issue #{issue_number} from {repo_name}...")
    connector = GitHubConnector()
    ctx = connector.fetch_issue(repo_name, issue_number)
    print(f"      Title: {ctx.title}")
    print(f"      Labels: {', '.join(ctx.labels) or 'none'}")

    openspec_text = connector.format_for_openspec(ctx)

    # Step 4: Clone repo
    project_cfg = None
    for proj in cfg.projects.values():
        if proj.repo == repo_name:
            project_cfg = proj
            break
    default_branch = project_cfg.default_branch if project_cfg else "main"
    clone_path = _clone_repo(repo_name, default_branch)

    # Steps 5-6: OpenSpec
    print("[2/7] Generating spec via OpenSpec...")
    bridge = OpenSpecBridge(str(clone_path))
    try:
        bridge.ensure_initialized()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("      (openspec init skipped)")

    change_name = f"issue-{issue_number}"
    bridge.create_change_proposal(openspec_text, change_name)

    # Step 7: Break into tasks
    print("[3/7] Breaking spec into parallelizable tasks...")
    tasks = break_down_spec(openspec_text)
    print(f"      Generated {len(tasks)} tasks")
    for t in tasks:
        print(f"        {t['id']}: {t['title']} ({t['complexity']})")

    # Step 8: Resolve waves
    print("[4/7] Resolving dependencies...")
    waves = resolve_dependencies(tasks)
    print(f"      {len(waves)} wave(s) of execution")

    # Step 9: Process waves
    spawner = AgentSpawner(cfg)
    results: dict[str, str] = {}  # task_id -> status

    for wave_num, wave in enumerate(waves, 1):
        print(f"\n[5/7] Wave {wave_num}/{len(waves)}: "
              f"spawning {len(wave)} agent(s)...")

        monitor = AgentMonitor(poll_interval=10)

        for task in wave:
            task_id = task["id"]
            wt_path, branch = _create_task_worktree(clone_path, task_id)
            print(f"      {task_id}: {task['title']} -> {wt_path}")

            # Copy openspec context into worktree
            src_openspec = clone_path / "openspec"
            dst_openspec = wt_path / "openspec"
            if src_openspec.exists():
                shutil.copytree(src_openspec, dst_openspec, dirs_exist_ok=True)

            prompt = task["description"]
            spawner.spawn_task(task_id, str(wt_path), prompt)

            info = spawner.running[task_id]
            monitor.register(
                task_id=task_id,
                handle=info["handle"],
                adapter=info["adapter"],
                worktree_path=str(wt_path),
                max_runtime=cfg.defaults.agent_timeout,
            )

        # Monitor wave
        print(f"[6/7] Monitoring wave {wave_num}...")

        def _on_done(tid: str, agent: object) -> None:
            results[tid] = "done"
            print(f"      {tid} completed")

        def _on_crashed(tid: str, agent: object) -> None:
            results[tid] = "crashed"
            print(f"      {tid} crashed!")

        def _on_stuck(tid: str, agent: object) -> None:
            results[tid] = "stuck"
            print(f"      {tid} stuck!")

        def _on_timeout(tid: str, agent: object) -> None:
            results[tid] = "timeout"
            print(f"      {tid} timed out!")

        monitor.on("done", _on_done)
        monitor.on("crashed", _on_crashed)
        monitor.on("stuck", _on_stuck)
        monitor.on("timeout", _on_timeout)

        await monitor.run()

    # Step 10: Summary
    print("\n[7/7] Summary:")
    done_count = sum(1 for s in results.values() if s == "done")
    fail_count = len(results) - done_count
    print(f"      Tasks completed: {done_count}/{len(results)}")
    if fail_count:
        print(f"      Tasks failed: {fail_count}")
    for tid, status in sorted(results.items()):
        wt = WORKTREES_DIR / tid
        print(f"        {tid}: {status} -> {wt}")


async def run_issue_full_pipeline(
    repo_name: str, issue_number: int
) -> None:
    """Full autonomous pipeline: issue -> code -> PR -> CI -> merge.

    Steps 1-9: Same as Phase 2 (fetch, spec, breakdown, parallel agents)
    Step 10: For each completed task push branch and create PR
    Step 11: Reactions engine handles CI failures and reviews
    Step 12: Print summary
    """
    cfg = load_config()

    # Steps 1-3
    print(f"[1/9] Fetching issue #{issue_number} from {repo_name}...")
    connector = GitHubConnector()
    ctx = connector.fetch_issue(repo_name, issue_number)
    print(f"      Title: {ctx.title}")
    print(f"      Labels: {', '.join(ctx.labels) or 'none'}")

    openspec_text = connector.format_for_openspec(ctx)

    # Step 4: Clone
    project_cfg = None
    for proj in cfg.projects.values():
        if proj.repo == repo_name:
            project_cfg = proj
            break
    default_branch = project_cfg.default_branch if project_cfg else "main"
    clone_path = _clone_repo(repo_name, default_branch)

    # Steps 5-6: OpenSpec
    print("[2/9] Generating spec via OpenSpec...")
    bridge = OpenSpecBridge(str(clone_path))
    try:
        bridge.ensure_initialized()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("      (openspec init skipped)")

    change_name = f"issue-{issue_number}"
    bridge.create_change_proposal(openspec_text, change_name)

    # Step 7: Break into tasks
    print("[3/9] Breaking spec into parallelizable tasks...")
    tasks = break_down_spec(openspec_text)
    print(f"      Generated {len(tasks)} tasks")

    # Step 8: Resolve waves
    print("[4/9] Resolving dependencies...")
    waves = resolve_dependencies(tasks)
    print(f"      {len(waves)} wave(s) of execution")

    # Step 9: Process waves
    spawner = AgentSpawner(cfg)
    completed_tasks: dict[str, dict] = {}  # task_id -> {branch, worktree}

    for wave_num, wave in enumerate(waves, 1):
        print(f"\n[5/9] Wave {wave_num}/{len(waves)}: "
              f"spawning {len(wave)} agent(s)...")

        monitor = AgentMonitor(poll_interval=10)

        for task in wave:
            task_id = task["id"]
            wt_path, branch = _create_task_worktree(clone_path, task_id)

            src_openspec = clone_path / "openspec"
            dst_openspec = wt_path / "openspec"
            if src_openspec.exists():
                shutil.copytree(src_openspec, dst_openspec, dirs_exist_ok=True)

            spawner.spawn_task(task_id, str(wt_path), task["description"])

            info = spawner.running[task_id]
            monitor.register(
                task_id=task_id,
                handle=info["handle"],
                adapter=info["adapter"],
                worktree_path=str(wt_path),
                max_runtime=cfg.defaults.agent_timeout,
            )

        def _on_done(tid: str, agent: object, _wave=wave) -> None:
            for t in _wave:
                if t["id"] == tid:
                    wt = WORKTREES_DIR / tid
                    completed_tasks[tid] = {
                        "branch": f"reknew/task/{tid}",
                        "worktree": str(wt),
                        "title": t["title"],
                    }
            print(f"      {tid} completed")

        def _on_failed(tid: str, agent: object) -> None:
            print(f"      {tid} failed")

        monitor.on("done", _on_done)
        monitor.on("crashed", _on_failed)
        monitor.on("stuck", _on_failed)
        monitor.on("timeout", _on_failed)

        await monitor.run()

    # Step 10: Push branches and create PRs
    print(f"\n[6/9] Pushing branches and creating PRs...")
    gh_api = GitHubAPI()
    pr_mgr = PRManager(gh_api, cfg)
    listener = WebhookListener(cfg, pr_mgr)
    reactions = ReactionsEngine(cfg, pr_mgr, spawner)

    pr_map: dict[str, int] = {}  # task_id -> pr_number

    for task_id, info in completed_tasks.items():
        branch = info["branch"]
        wt = info["worktree"]
        title = info["title"]

        pr_mgr.push_branch(wt, branch)
        result = pr_mgr.create_pr(
            repo_name=repo_name,
            branch=branch,
            base=default_branch,
            title=title,
            body=f"Automated implementation for task {task_id}.",
            task_id=task_id,
            issue_number=issue_number,
        )
        pr_map[task_id] = result.pr_number
        listener.watch_pr(result.pr_number, task_id)
        print(f"      {task_id}: PR #{result.pr_number} -> {result.pr_url}")

    # Step 11: Monitor PRs for CI and reviews
    if pr_map:
        print("[7/9] Monitoring PRs for CI and reviews...")

        def _on_ci(task_id: str, pr_number: int, data: object) -> None:
            asyncio.get_event_loop().create_task(
                reactions.handle_event(
                    "ci-completed", task_id, pr_number, repo_name
                )
            )

        def _on_review(task_id: str, pr_number: int, data: object) -> None:
            asyncio.get_event_loop().create_task(
                reactions.handle_event(
                    "review-submitted", task_id, pr_number, repo_name
                )
            )

        listener.on("ci-completed", _on_ci)
        listener.on("review-submitted", _on_review)

        # Run polling loop with a timeout
        try:
            await asyncio.wait_for(
                listener.poll_loop(repo_name),
                timeout=cfg.defaults.agent_timeout,
            )
        except asyncio.TimeoutError:
            print("      PR monitoring timed out")

    # Step 12: Summary
    print("\n[8/9] Summary:")
    print(f"      Tasks completed: {len(completed_tasks)}/{len(tasks)}")
    for tid, pr_num in pr_map.items():
        print(f"        {tid}: PR #{pr_num}")
    print("[9/9] Pipeline complete.")


def main() -> None:
    """CLI entry point. Parse args and run.

    Usage:
        python -m reknew.main <repo> <issue_number>
        python -m reknew.main --parallel <repo> <issue_number>
        python -m reknew.main --pipeline <repo> <issue_number>

    Examples:
        python -m reknew.main ReKnew-Data-and-AI/sp-enablers-slayer 114
        python -m reknew.main --parallel ReKnew-Data-and-AI/sp-enablers-slayer 114
        python -m reknew.main --pipeline ReKnew-Data-and-AI/sp-enablers-slayer 114
    """
    args = sys.argv[1:]
    mode = "single"

    if "--parallel" in args:
        mode = "parallel"
        args.remove("--parallel")
    elif "--pipeline" in args:
        mode = "pipeline"
        args.remove("--pipeline")

    if len(args) != 2:
        print("Usage: python -m reknew.main "
              "[--parallel|--pipeline] <repo> <issue_number>")
        sys.exit(1)

    repo_name = args[0]
    try:
        issue_number = int(args[1])
    except ValueError:
        print(f"Error: issue_number must be an integer, got '{args[1]}'")
        sys.exit(1)

    if not _check_prerequisites():
        sys.exit(1)

    _ensure_dirs()

    if mode == "pipeline":
        asyncio.run(run_issue_full_pipeline(repo_name, issue_number))
    elif mode == "parallel":
        asyncio.run(run_issue_parallel(repo_name, issue_number))
    else:
        asyncio.run(run_single_issue(repo_name, issue_number))


if __name__ == "__main__":
    main()
