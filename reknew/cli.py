"""CLI entry point for ReKnew AI-SDLC Platform."""

import asyncio
import sys

import click

from reknew.config import load_config


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
    asyncio.run(daemon.run())


@main.command()
@click.argument("repo")
@click.argument("issue", type=int)
@click.option("--parallel", is_flag=True, help="Use parallel agents")
@click.option("--pipeline", is_flag=True, help="Full autonomous pipeline")
def process(repo: str, issue: int, parallel: bool, pipeline: bool) -> None:
    """Process a single issue through the pipeline."""
    from reknew.main import (
        _check_prerequisites,
        _ensure_dirs,
        run_issue_full_pipeline,
        run_issue_parallel,
        run_single_issue,
    )

    if not _check_prerequisites():
        sys.exit(1)
    _ensure_dirs()

    if pipeline:
        asyncio.run(run_issue_full_pipeline(repo, issue))
    elif parallel:
        asyncio.run(run_issue_parallel(repo, issue))
    else:
        asyncio.run(run_single_issue(repo, issue))


@main.command()
def status() -> None:
    """Show status of all running agents and tasks."""
    import json

    cfg = load_config()
    click.echo(f"ReKnew Status")
    click.echo(f"  Projects: {len(cfg.projects)}")
    for name, proj in cfg.projects.items():
        click.echo(f"    {name}: {proj.repo}")
    click.echo(f"  Default agent: {cfg.defaults.agent}")
    click.echo(f"  Max parallel: {cfg.defaults.max_parallel_agents}")
    click.echo(f"  GitHub mode: {cfg.github.mode}")


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
