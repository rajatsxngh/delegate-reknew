"""CLI entry point for ReKnew AI-SDLC Platform."""

import asyncio
import os
import sys
import time
from pathlib import Path

import click

from reknew.config import load_config


REKNEW_HOME = Path.home() / ".reknew"
WORKTREES_DIR = REKNEW_HOME / "worktrees"
LOGS_DIR = REKNEW_HOME / "logs"


@click.group()
def main() -> None:
    """ReKnew AI-SDLC Platform"""


@main.command()
@click.option("--port", default=3000, help="Dashboard port")
@click.option("--config", "config_path", default="reknew.yaml",
              help="Path to reknew.yaml")
def start(port: int, config_path: str) -> None:
    """Start the ReKnew daemon with dashboard."""
    from reknew.daemon import ReknewDaemon

    cfg = load_config(config_path)
    if port != cfg.port:
        cfg.port = port

    daemon = ReknewDaemon(config_path)
    daemon.config.port = port
    click.echo(f"Starting ReKnew daemon on port {port}...")
    click.echo(f"  Dashboard: http://localhost:{port}/api/health")
    click.echo(f"  Projects: http://localhost:{port}/api/projects")
    click.echo(f"  Tasks: http://localhost:{port}/api/tasks")
    click.echo(f"  Agents: http://localhost:{port}/api/agents")
    click.echo(f"  Capacity: http://localhost:{port}/api/capacity")
    click.echo("  Press Ctrl+C to stop.")
    asyncio.run(daemon.run())


@main.command()
@click.argument("repo")
@click.argument("issue", type=int)
@click.option("--parallel", is_flag=True, help="Use parallel agents")
@click.option("--pipeline", is_flag=True, help="Full autonomous pipeline")
@click.option("--watch", is_flag=True,
              help="Watch CI status after PR creation")
def process(repo: str, issue: int, parallel: bool, pipeline: bool,
            watch: bool) -> None:
    """Process a single issue through the pipeline."""
    from reknew.main import (
        _check_prerequisites,
        _ensure_dirs,
        run_issue_parallel,
        run_single_issue,
    )

    if not _check_prerequisites():
        sys.exit(1)
    _ensure_dirs()

    if pipeline or parallel:
        asyncio.run(run_issue_parallel(repo, issue, watch=watch))
    else:
        asyncio.run(run_single_issue(repo, issue, watch=watch))


@main.command()
def status() -> None:
    """Show status of worktrees, running agents, and recent logs."""
    # Active worktrees
    click.echo("=== Active Worktrees ===")
    if WORKTREES_DIR.exists():
        worktrees = sorted(WORKTREES_DIR.iterdir())
        if worktrees:
            for wt in worktrees:
                if wt.is_dir():
                    # Check git branch
                    import subprocess
                    result = subprocess.run(
                        ["git", "branch", "--show-current"],
                        cwd=wt,
                        capture_output=True,
                        text=True,
                    )
                    branch = result.stdout.strip() if result.returncode == 0 else "unknown"
                    click.echo(f"  {wt.name}: {wt} (branch: {branch})")
        else:
            click.echo("  (none)")
    else:
        click.echo("  (directory does not exist)")

    # Running agent processes
    click.echo("\n=== Running Agent Processes ===")
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-af", "claude.*--print.*--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                click.echo(f"  {line}")
        else:
            click.echo("  (none running)")
    except Exception:
        click.echo("  (could not check processes)")

    # Recent log files
    click.echo("\n=== Recent Logs ===")
    if LOGS_DIR.exists():
        logs = sorted(LOGS_DIR.glob("*.log"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True)
        if logs:
            for log in logs[:10]:
                mtime = log.stat().st_mtime
                age = time.time() - mtime
                size = log.stat().st_size
                if age < 60:
                    age_str = f"{int(age)}s ago"
                elif age < 3600:
                    age_str = f"{int(age / 60)}m ago"
                elif age < 86400:
                    age_str = f"{int(age / 3600)}h ago"
                else:
                    age_str = f"{int(age / 86400)}d ago"
                click.echo(f"  {log.name}: {size:,} bytes, {age_str}")
        else:
            click.echo("  (no logs)")
    else:
        click.echo("  (directory does not exist)")

    # Config summary
    click.echo("\n=== Configuration ===")
    try:
        cfg = load_config()
        click.echo(f"  Projects: {len(cfg.projects)}")
        for name, proj in cfg.projects.items():
            click.echo(f"    {name}: {proj.repo}")
        click.echo(f"  Default agent: {cfg.defaults.agent}")
        click.echo(f"  Max parallel: {cfg.defaults.max_parallel_agents}")
        click.echo(f"  GitHub mode: {cfg.github.mode}")
    except Exception as exc:
        click.echo(f"  (config not loaded: {exc})")


@main.command("config")
@click.option("--path", default="reknew.yaml", help="Path to reknew.yaml")
def validate_config(path: str) -> None:
    """Validate and display current configuration."""
    try:
        cfg = load_config(path)
        click.echo(f"Configuration valid: {path}")
        click.echo(f"  Port: {cfg.port}")
        click.echo(f"  Projects: {len(cfg.projects)}")
        for name, proj in cfg.projects.items():
            click.echo(f"    {name}: {proj.repo} (branch: {proj.default_branch})")
        click.echo(f"  Agent: {cfg.defaults.agent}")
        click.echo(f"  Workspace: {cfg.defaults.workspace}")
        click.echo(f"  Reactions: {list(cfg.reactions.keys())}")
        click.echo(f"  Capacity rules: {len(cfg.capacity.rules)}")
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
