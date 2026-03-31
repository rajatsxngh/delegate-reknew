# AGENTS.md — ReKnew AI-SDLC Platform

## Project context

This is a fork of [Delegate](https://github.com/nikhilgarg28/delegate) (MIT licensed). Delegate provides an orchestration engine for AI coding agents. ReKnew adds a business layer on top: GitHub/JIRA integration, LLM-agnostic adapters, capacity management routing, a reactions engine, and an executive dashboard.

## Critical rules

1. **DO NOT modify files in the `delegate/` directory** unless the spec explicitly says to. ReKnew code lives in `reknew/`.
2. **All git topology operations (worktree, branch, merge, rebase, push) happen ONLY in the daemon.** Agents never run these commands.
3. **All external API calls (GitHub, LLM) must have error handling.** Catch specific exceptions, log descriptive messages, and either retry or escalate.
4. **Type hints on every function signature.** Use Python 3.12+ syntax (list[str] not List[str]).
5. **Every public function has a Google-style docstring** with Args, Returns, Raises sections.
6. **Tests use pytest with async support** (pytest-asyncio). Mock external APIs using unittest.mock.
7. **Config drives behavior.** Don't hardcode values that should come from reknew.yaml.

## Architecture

```
ReKnew Layer (reknew/) ──→ Delegate Core (delegate/) ──→ LLM Adapters
     │                              │
     │ Issue connector              │ Daemon event loop
     │ OpenSpec bridge              │ Worktree manager
     │ Capacity router              │ Task state machine
     │ Reactions engine             │ Agent monitor
     │ GitHub PR integration        │ Agent-reviews-agent
     │ YAML config                  │ Merge worker
     │ Dashboard                    │ Sandboxing (6 layers)
     │                              │ Web UI
```

## Coding standards

- Python 3.12+, f-strings, dataclasses, Enum, ABC
- Max line length: 100 characters
- Imports: stdlib → third-party → local, separated by blank lines
- No wildcard imports
- async/await for all I/O in the daemon
- subprocess for git commands and agent spawning (never os.system)
- Use Path from pathlib (not os.path.join)
- Logging: use Python's logging module with named loggers

## Dependencies

- PyGithub: GitHub API
- FastAPI + uvicorn: HTTP/WebSocket server
- anthropic: LLM API for task breakdown
- httpx: HTTP client for webhooks and JIRA
- PyYAML: config loading
- click + rich: CLI
- pytest + pytest-asyncio: testing

## Environment variables

- GITHUB_TOKEN (required): GitHub personal access token with `repo` scope
- ANTHROPIC_API_KEY (required): for Claude Code adapter and task breakdown
- OPENAI_API_KEY (optional): for Codex adapter
- GOOGLE_API_KEY (optional): for Gemini adapter

## Build order

1. Phase 1: `specs/phase1-issue-connector.md` — config, GitHub connector, OpenSpec bridge, first run
2. Phase 2: `specs/phase2-llm-adapters.md` — adapter interface, task breakdown, parallel agents
3. Phase 3: `specs/phase3-github-reactions.md` — GitHub PRs, CI monitoring, reactions engine
4. Phase 4: `specs/phase4-dashboard.md` — capacity router, dashboard, executive view

Each spec has exact class signatures, method signatures, error handling requirements, and test cases.
