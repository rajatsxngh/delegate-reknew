"""Entry point -- processes GitHub issues via single agent or parallel agents."""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from reknew.config import load_config, ReknewConfig
from reknew.connectors.github_connector import GitHubConnector, IssueContext
from reknew import state

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

    if not shutil.which("claude"):
        print("Error: Claude Code CLI not found.")
        print("  Install: npm install -g @anthropic-ai/claude-code")
        ok = False

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
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=clone_path,
            capture_output=True,
            text=True,
        )
        if worktree_path.exists():
            shutil.rmtree(worktree_path)

    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=clone_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


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
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=clone_path,
            capture_output=True,
            text=True,
        )
        if worktree_path.exists():
            shutil.rmtree(worktree_path)

    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
        cwd=clone_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


def _build_agent_prompt(ctx: IssueContext, cfg: ReknewConfig) -> str:
    """Build a direct agent prompt from the issue context.

    No OpenSpec dependency. Includes issue description, file tree,
    README, related files, and clear implementation instructions.

    Args:
        ctx: populated IssueContext from GitHubConnector
        cfg: validated ReknewConfig

    Returns:
        Complete prompt string for the coding agent.
    """
    test_command = ""
    for proj in cfg.projects.values():
        if proj.repo == ctx.repo_name:
            test_command = proj.test_command
            break

    parts: list[str] = []
    parts.append(
        "You are implementing a code change based on a GitHub issue. "
        "Read the full context below, then implement the requested changes."
    )

    parts.append(f"\n# Issue #{ctx.issue_number}: {ctx.title}")
    parts.append(f"\n## Description\n{ctx.body or '(no description)'}")

    if ctx.labels:
        parts.append(f"\n## Labels\n{', '.join(ctx.labels)}")

    if ctx.comments:
        parts.append("\n## Discussion")
        for i, comment in enumerate(ctx.comments, 1):
            parts.append(f"Comment {i}: {comment}")

    if ctx.file_tree:
        parts.append("\n## Repository file structure")
        parts.append("```")
        for entry in ctx.file_tree:
            parts.append(entry)
        parts.append("```")

    if ctx.readme_content:
        parts.append("\n## README (excerpt)")
        parts.append(ctx.readme_content)

    if ctx.related_files:
        parts.append("\n## Related files (contents)")
        for rf in ctx.related_files:
            parts.append(f"\n### {rf['path']}")
            parts.append("```")
            parts.append(rf["content"])
            parts.append("```")

    parts.append("\n## Instructions")
    parts.append(
        "1. Read and understand the existing codebase.\n"
        "2. Implement ALL the changes requested in the issue.\n"
        "3. Follow existing code patterns and conventions.\n"
        "4. Add or update tests for any new functionality.\n"
        "5. Make sure all existing tests still pass."
    )
    if test_command:
        parts.append(f"6. Run the test suite with: {test_command}")
    else:
        parts.append(
            "6. Run tests if a test runner is configured in the project."
        )
    parts.append(
        "7. Keep changes minimal and focused on the issue requirements.\n"
        "8. Do not modify unrelated files."
    )

    return "\n".join(parts)


def _spawn_agent(
    worktree_path: Path, prompt: str, task_id: str
) -> subprocess.Popen:
    """Spawn Claude Code in the worktree with the agent prompt.

    Args:
        worktree_path: Working directory for the agent
        prompt: The agent's instruction prompt
        task_id: For log file naming

    Returns:
        The running Popen process.
    """
    log_path = LOGS_DIR / f"{task_id}.log"

    log_file = open(log_path, "w")
    process = subprocess.Popen(
        ["claude", "--print", "--dangerously-skip-permissions", prompt],
        cwd=worktree_path,
        stdout=log_file,
        stderr=log_file,
    )
    return process


def _commit_changes(worktree_path: Path, message: str) -> bool:
    """Stage and commit any uncommitted changes in the worktree.

    Args:
        worktree_path: Path to the git worktree
        message: Commit message

    Returns:
        True if changes were committed, False if nothing to commit.
    """
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return False

    subprocess.run(
        ["git", "add", "-A"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return True


def _push_and_create_pr(
    worktree_path: Path,
    branch_name: str,
    repo_name: str,
    default_branch: str,
    title: str,
    task_id: str,
    issue_number: int,
    cfg: ReknewConfig,
) -> tuple[str | None, int | None]:
    """Push branch and create a PR on GitHub.

    Returns:
        Tuple of (pr_url, pr_number), or (None, None) on failure.
    """
    from reknew.github.api import GitHubAPI
    from reknew.github.pr_manager import PRManager

    try:
        result = subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "push", "--force-with-lease", "origin", branch_name],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            )

        gh_api = GitHubAPI()
        pr_mgr = PRManager(gh_api, cfg)
        pr_result = pr_mgr.create_pr(
            repo_name=repo_name,
            branch=branch_name,
            base=default_branch,
            title=title,
            body=(f"Automated implementation for task {task_id}.\n\n"
                  f"Closes #{issue_number}"),
            task_id=task_id,
            issue_number=issue_number,
        )
        return pr_result.pr_url, pr_result.pr_number
    except Exception as exc:
        print(f"      Error creating PR: {exc}")
        return None, None


async def _watch_pr_ci(
    repo_name: str,
    pr_number: int,
    task_id: str,
    cfg: ReknewConfig,
    worktree_path: Path,
    branch_name: str,
) -> None:
    """Poll PR for CI status and react to failures."""
    from reknew.github.api import GitHubAPI
    from reknew.github.pr_manager import PRManager
    from reknew.adapters.claude_adapter import ClaudeCodeAdapter

    gh_api = GitHubAPI()
    pr_mgr = PRManager(gh_api, cfg)

    poll_interval = cfg.github.poll_interval
    ci_rule = cfg.reactions.get("ci-failed")
    max_retries = ci_rule.retries if ci_rule else 2
    retry_count = 0
    timeout = cfg.defaults.agent_timeout

    print(f"      Watching PR #{pr_number} for CI results "
          f"(polling every {poll_interval}s, timeout {timeout}s)...")

    elapsed = 0
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        try:
            ci = pr_mgr.get_ci_status(repo_name, pr_number)
        except Exception as exc:
            print(f"      Error checking CI: {exc}")
            continue

        if ci.overall == "pending":
            state.update_task(task_id, ci_status="pending")
            print(f"      CI still pending... ({elapsed}s elapsed)")
            continue

        if ci.overall == "passed":
            state.update_task(task_id, state="ci_passed", ci_status="passed")
            print(f"\n      CI passed on PR #{pr_number}!")
            return

        if ci.overall == "failed":
            state.update_task(task_id, ci_status="failed")
            print(f"\n      CI failed on PR #{pr_number}")
            if not (ci_rule and ci_rule.auto):
                print("      Auto-fix disabled in config. Stopping watch.")
                return

            if retry_count >= max_retries:
                print(
                    f"      Retries exhausted ({retry_count}/{max_retries})."
                )
                return

            retry_count += 1
            print(f"      Retry {retry_count}/{max_retries}: "
                  "re-spawning agent with CI feedback...")

            feedback = pr_mgr.format_ci_errors(ci)
            adapter = ClaudeCodeAdapter(
                stuck_timeout=cfg.defaults.stuck_timeout
            )
            handle = adapter.spawn(
                str(worktree_path), feedback,
                f"{task_id}-fix{retry_count}",
            )

            while adapter.poll_status(handle).value == "running":
                await asyncio.sleep(5)

            committed = _commit_changes(
                worktree_path,
                f"fix: address CI failures (retry {retry_count})"
            )
            if committed:
                subprocess.run(
                    ["git", "push", "origin", branch_name],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                )
                print("      Fix pushed. Waiting for CI to re-run...")
            else:
                print("      Agent made no changes. Stopping.")
                return

    print(f"      Watch timed out after {timeout}s")


async def run_single_issue(
    repo_name: str,
    issue_number: int,
    watch: bool = False,
) -> None:
    """Process one issue through the single-agent pipeline.

    Writes state updates to ~/.reknew/state.json at each step so the
    dashboard can display real-time progress.

    Args:
        repo_name: "owner/repo" format
        issue_number: GitHub issue number
        watch: if True, poll CI status after PR creation
    """
    task_id = f"T{issue_number:04d}"

    # Step 1: Load config
    cfg = load_config()

    # Step 2: Fetch issue
    print(f"[1/5] Fetching issue #{issue_number} from {repo_name}...")
    state.update_task(
        task_id,
        title=f"Issue #{issue_number}",
        state="fetching",
        repo=repo_name,
        issue_number=issue_number,
        agent_type="claude-code",
    )
    state.update_capacity(total=1, human=0, ai=1)
    connector = GitHubConnector()
    ctx = connector.fetch_issue(repo_name, issue_number)
    print(f"      Title: {ctx.title}")
    print(f"      Labels: {', '.join(ctx.labels) or 'none'}")
    print(f"      Files in repo: {len(ctx.file_tree)}")
    state.update_task(
        task_id,
        title=ctx.title,
        state="fetched",
        labels=ctx.labels,
    )

    # Step 3: Build agent prompt directly
    print("[2/5] Building agent prompt...")
    prompt = _build_agent_prompt(ctx, cfg)
    state.update_task(task_id, state="prompt_built")

    # Step 4: Clone repo
    project_cfg = None
    for proj in cfg.projects.values():
        if proj.repo == repo_name:
            project_cfg = proj
            break
    default_branch = project_cfg.default_branch if project_cfg else "main"
    clone_path = _clone_repo(repo_name, default_branch)

    # Step 5: Create worktree
    print("[3/5] Creating isolated worktree...")
    worktree_path, branch_name = _create_worktree(clone_path, issue_number)
    print(f"      Worktree: {worktree_path}")
    print(f"      Branch: {branch_name}")
    state.update_task(
        task_id,
        state="worktree_created",
        worktree=str(worktree_path),
        branch=branch_name,
    )

    # Step 6: Spawn agent
    print("[4/5] Spawning Claude Code agent...")
    log_path = LOGS_DIR / f"{task_id}.log"
    process = _spawn_agent(worktree_path, prompt, task_id)
    state.update_task(
        task_id,
        state="agent_running",
        log_file=str(log_path),
        pid=process.pid,
    )

    # Step 7: Poll
    print(f"[5/5] Agent working... (logs: {log_path})")
    while process.poll() is None:
        # Update log size in state for progress tracking
        try:
            log_size = log_path.stat().st_size
        except OSError:
            log_size = 0
        state.update_task(task_id, log_bytes=log_size)
        print("      Still working...")
        await asyncio.sleep(10)

    # Step 8: Report results
    rc = process.returncode
    if rc != 0:
        print(f"\nAgent failed (exit code {rc}).")
        print(f"  View logs: cat {log_path}")
        state.update_task(task_id, state="agent_failed", exit_code=rc)
        try:
            lines = log_path.read_text().splitlines()
            tail = lines[-20:] if len(lines) > 20 else lines
            print("  --- Last lines of log ---")
            for line in tail:
                print(f"  {line}")
        except Exception:
            pass
        return

    print(f"\nAgent completed successfully!")
    print(f"  View logs: cat {log_path}")

    # Count files changed
    diff_stat = subprocess.run(
        ["git", "diff", "--stat", f"origin/{default_branch}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    files_changed = len([
        l for l in diff_stat.stdout.strip().split("\n")
        if l.strip() and "|" in l
    ])

    state.update_task(
        task_id,
        state="agent_done",
        exit_code=0,
        files_changed=files_changed,
    )

    # Step 9: Commit uncommitted changes
    committed = _commit_changes(
        worktree_path,
        f"feat: implement issue #{issue_number} - {ctx.title}"
    )
    if committed:
        print("  Committed agent's uncommitted changes.")

    # Check if there are any changes vs base branch
    diff_result = subprocess.run(
        ["git", "diff", f"origin/{default_branch}..HEAD", "--stat"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if not diff_result.stdout.strip():
        print("  No changes were made. Skipping PR creation.")
        state.update_task(task_id, state="no_changes")
        return

    print(f"  Changes:\n{diff_result.stdout.strip()}")

    # Step 10: Push and create PR
    print("\nPushing branch and creating PR...")
    state.update_task(task_id, state="pushing")
    pr_url, pr_number = _push_and_create_pr(
        worktree_path=worktree_path,
        branch_name=branch_name,
        repo_name=repo_name,
        default_branch=default_branch,
        title=f"Issue #{issue_number}: {ctx.title}",
        task_id=task_id,
        issue_number=issue_number,
        cfg=cfg,
    )
    if pr_url:
        print(f"  PR created: {pr_url}")
        state.update_task(
            task_id,
            state="pr_created",
            pr_url=pr_url,
            pr_number=pr_number,
        )

        if watch:
            await _watch_pr_ci(
                repo_name, pr_number, task_id, cfg,
                worktree_path, branch_name,
            )
    else:
        state.update_task(task_id, state="pr_failed")


async def run_issue_parallel(
    repo_name: str,
    issue_number: int,
    watch: bool = False,
) -> None:
    """Process an issue with parallel agents.

    Falls back to single-agent mode if ANTHROPIC_API_KEY is not set.

    Args:
        repo_name: "owner/repo" format
        issue_number: GitHub issue number
        watch: if True, poll CI status after PR creation
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Warning: ANTHROPIC_API_KEY not set. "
              "Falling back to single-agent mode.")
        await run_single_issue(repo_name, issue_number, watch=watch)
        return

    from reknew.orchestration.task_breakdown import break_down_spec
    from reknew.orchestration.dependency import resolve_dependencies
    from reknew.agents.spawner import AgentSpawner
    from reknew.agents.monitor import AgentMonitor

    cfg = load_config()

    # Step 1: Fetch issue
    print(f"[1/7] Fetching issue #{issue_number} from {repo_name}...")
    connector = GitHubConnector()
    ctx = connector.fetch_issue(repo_name, issue_number)
    print(f"      Title: {ctx.title}")
    print(f"      Labels: {', '.join(ctx.labels) or 'none'}")

    spec_text = connector.format_for_openspec(ctx)

    # Clone repo
    project_cfg = None
    for proj in cfg.projects.values():
        if proj.repo == repo_name:
            project_cfg = proj
            break
    default_branch = project_cfg.default_branch if project_cfg else "main"
    clone_path = _clone_repo(repo_name, default_branch)

    # Break into tasks
    print("[2/7] Breaking spec into parallelizable tasks...")
    try:
        tasks = break_down_spec(spec_text)
    except Exception as exc:
        print(f"      Task breakdown failed: {exc}")
        print("      Falling back to single-agent mode.")
        await run_single_issue(repo_name, issue_number, watch=watch)
        return

    print(f"      Generated {len(tasks)} tasks")
    for t in tasks:
        print(f"        {t['id']}: {t['title']} ({t['complexity']})")
        state.update_task(
            t["id"],
            title=t["title"],
            state="queued",
            repo=repo_name,
            issue_number=issue_number,
            complexity=t["complexity"],
            agent_type="claude-code",
        )

    state.update_capacity(total=len(tasks), human=0, ai=len(tasks))

    # Resolve waves
    print("[3/7] Resolving dependencies...")
    waves = resolve_dependencies(tasks)
    print(f"      {len(waves)} wave(s) of execution")

    # Process waves
    spawner = AgentSpawner(cfg)
    results: dict[str, dict] = {}

    for wave_num, wave in enumerate(waves, 1):
        print(f"\n[4/7] Wave {wave_num}/{len(waves)}: "
              f"spawning {len(wave)} agent(s)...")

        monitor = AgentMonitor(poll_interval=10)

        for task in wave:
            task_id = task["id"]
            wt_path, branch = _create_task_worktree(clone_path, task_id)
            print(f"      {task_id}: {task['title']} -> {wt_path}")

            state.update_task(
                task_id,
                state="agent_running",
                worktree=str(wt_path),
                branch=branch,
            )

            spawner.spawn_task(task_id, str(wt_path), task["description"])

            info = spawner.running[task_id]
            monitor.register(
                task_id=task_id,
                handle=info["handle"],
                adapter=info["adapter"],
                worktree_path=str(wt_path),
                max_runtime=cfg.defaults.agent_timeout,
            )

            results[task_id] = {
                "status": "running",
                "worktree": str(wt_path),
                "branch": branch,
                "title": task["title"],
            }

        print(f"[5/7] Monitoring wave {wave_num}...")

        def _on_done(tid: str, agent: object) -> None:
            results[tid]["status"] = "done"
            state.update_task(tid, state="agent_done")
            print(f"      {tid} completed")

        def _on_crashed(tid: str, agent: object) -> None:
            results[tid]["status"] = "crashed"
            state.update_task(tid, state="agent_failed")
            print(f"      {tid} crashed!")

        def _on_stuck(tid: str, agent: object) -> None:
            results[tid]["status"] = "stuck"
            state.update_task(tid, state="agent_stuck")
            print(f"      {tid} stuck!")

        def _on_timeout(tid: str, agent: object) -> None:
            results[tid]["status"] = "timeout"
            state.update_task(tid, state="agent_timeout")
            print(f"      {tid} timed out!")

        monitor.on("done", _on_done)
        monitor.on("crashed", _on_crashed)
        monitor.on("stuck", _on_stuck)
        monitor.on("timeout", _on_timeout)

        await monitor.run()

    # Create PRs for completed tasks
    print(f"\n[6/7] Creating PRs for completed tasks...")
    pr_urls: dict[str, str] = {}

    for task_id, info in results.items():
        if info["status"] != "done":
            print(f"      {task_id}: skipped (status: {info['status']})")
            continue

        wt_path = Path(info["worktree"])
        branch = info["branch"]
        title = info["title"]

        committed = _commit_changes(
            wt_path, f"feat: {title} (task {task_id})"
        )
        if committed:
            print(f"      {task_id}: committed uncommitted changes")

        diff_result = subprocess.run(
            ["git", "diff", f"origin/{default_branch}..HEAD", "--stat"],
            cwd=wt_path,
            capture_output=True,
            text=True,
        )
        if not diff_result.stdout.strip():
            print(f"      {task_id}: no changes, skipping PR")
            state.update_task(task_id, state="no_changes")
            continue

        state.update_task(task_id, state="pushing")
        pr_url, pr_number = _push_and_create_pr(
            worktree_path=wt_path,
            branch_name=branch,
            repo_name=repo_name,
            default_branch=default_branch,
            title=f"{title} (issue #{issue_number})",
            task_id=task_id,
            issue_number=issue_number,
            cfg=cfg,
        )
        if pr_url:
            pr_urls[task_id] = pr_url
            state.update_task(
                task_id,
                state="pr_created",
                pr_url=pr_url,
                pr_number=pr_number,
            )
            print(f"      {task_id}: PR -> {pr_url}")

    # Summary
    print("\n[7/7] Summary:")
    done_count = sum(1 for r in results.values() if r["status"] == "done")
    fail_count = len(results) - done_count
    print(f"      Tasks completed: {done_count}/{len(results)}")
    if fail_count:
        print(f"      Tasks failed: {fail_count}")
    print(f"      PRs created: {len(pr_urls)}")
    for tid, url in pr_urls.items():
        print(f"        {tid}: {url}")

    if watch and pr_urls:
        print("\nWatching PRs for CI results...")
        for tid, url in pr_urls.items():
            pr_number = int(url.rstrip("/").split("/")[-1])
            info = results[tid]
            await _watch_pr_ci(
                repo_name, pr_number, tid, cfg,
                Path(info["worktree"]), info["branch"],
            )


def main() -> None:
    """CLI entry point. Parse args and run.

    Usage:
        python -m reknew.main <repo> <issue_number>
        python -m reknew.main --parallel <repo> <issue_number>
        python -m reknew.main --watch <repo> <issue_number>
        python -m reknew.main --parallel --watch <repo> <issue_number>
    """
    args = sys.argv[1:]
    mode = "single"
    watch = False

    if "--parallel" in args:
        mode = "parallel"
        args.remove("--parallel")
    elif "--pipeline" in args:
        mode = "parallel"
        args.remove("--pipeline")

    if "--watch" in args:
        watch = True
        args.remove("--watch")

    if len(args) != 2:
        print("Usage: python -m reknew.main "
              "[--parallel] [--watch] <repo> <issue_number>")
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

    if mode == "parallel":
        asyncio.run(run_issue_parallel(repo_name, issue_number, watch=watch))
    else:
        asyncio.run(run_single_issue(repo_name, issue_number, watch=watch))


if __name__ == "__main__":
    main()
