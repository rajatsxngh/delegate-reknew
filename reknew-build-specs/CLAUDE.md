# CLAUDE.md — ReKnew AI-SDLC Platform

## Project overview

ReKnew AI-SDLC is built as a layer on top of a forked copy of [Delegate](https://github.com/nikhilgarg28/delegate) (MIT licensed, Python). Delegate provides the orchestration engine (daemon, worktrees, merge worker, sandboxing). ReKnew adds: GitHub/JIRA integration, LLM-agnostic adapters, capacity management routing, a reactions engine for CI feedback, and an executive dashboard.

All ReKnew code lives in the `reknew/` directory. Do NOT modify files in `delegate/` except for the agent spawner refactor described in `specs/phase2-llm-adapters.md`.

## Architecture

```
GitHub/JIRA issues
       ↓
┌─────────────────────────────┐
│   ReKnew Layer (reknew/)    │  ← ALL NEW CODE
│   - Issue connector         │
│   - OpenSpec bridge         │
│   - Capacity router         │
│   - Reactions engine        │
│   - YAML config             │
│   - GitHub PR integration   │
│   - Capacity dashboard      │
└─────────────┬───────────────┘
              ↓ feeds tasks into
┌─────────────────────────────┐
│   Delegate Core (delegate/) │  ← DON'T TOUCH
│   - Daemon event loop       │
│   - Worktree manager        │
│   - Task state machine      │
│   - Agent monitor           │
│   - Agent-reviews-agent     │
│   - Merge worker            │
│   - Sandboxing              │
│   - Web UI                  │
└─────────────┬───────────────┘
              ↓
┌─────────────────────────────┐
│   LLM Adapter Layer         │  ← MODIFY delegate/agents/
│   Claude, Codex, Gemini,    │
│   Amp, Aider, Local LLMs    │
└─────────────────────────────┘
```

## Tech stack

- Python 3.12+
- FastAPI for HTTP/WebSocket API
- asyncio for daemon event loop
- PyGithub for GitHub API
- graphlib.TopologicalSorter for dependency resolution
- subprocess.Popen + git worktree for agent isolation
- SQLite for task storage (via Delegate)
- React for dashboard frontend (extends Delegate's existing UI)
- YAML for configuration (PyYAML)
- OpenSpec CLI for spec generation (external tool, invoked by agents)

## File structure

```
delegate-reknew/
├── delegate/                  # Delegate's code — DO NOT MODIFY (except agents/)
├── reknew/                    # All ReKnew code
│   ├── __init__.py
│   ├── main.py                # Entry point
│   ├── config.py              # YAML config loader + validator
│   ├── connectors/
│   │   ├── __init__.py
│   │   ├── github_connector.py
│   │   └── jira_connector.py
│   ├── spec/
│   │   ├── __init__.py
│   │   └── openspec_bridge.py
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── task_breakdown.py
│   │   ├── dependency.py
│   │   └── capacity_router.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base_adapter.py
│   │   ├── claude_adapter.py
│   │   ├── codex_adapter.py
│   │   ├── gemini_adapter.py
│   │   └── local_adapter.py
│   ├── reactions/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── ci_handler.py
│   │   └── review_handler.py
│   ├── github/
│   │   ├── __init__.py
│   │   ├── pr_manager.py
│   │   ├── webhook_listener.py
│   │   └── api.py
│   └── dashboard/
│       ├── __init__.py
│       ├── api.py
│       └── websocket.py
├── tests/
│   ├── test_config.py
│   ├── test_github_connector.py
│   ├── test_task_breakdown.py
│   ├── test_dependency.py
│   ├── test_capacity_router.py
│   ├── test_reactions.py
│   └── test_pr_manager.py
├── specs/                     # Detailed build specs (read these first)
│   ├── phase1-issue-connector.md
│   ├── phase2-llm-adapters.md
│   ├── phase3-github-reactions.md
│   └── phase4-dashboard.md
├── reknew.yaml                # Config file
├── pyproject.toml             # Dependencies
└── README.md
```

## Coding conventions

- Type hints on all function signatures
- Docstrings on all public methods (Google style)
- f-strings for string formatting (never .format() or %)
- dataclasses for data containers
- Enum for finite state values
- ABC for abstract interfaces
- async/await for all I/O operations in the daemon
- pytest for all tests
- All imports at file top, stdlib first, then third-party, then local
- Max line length: 100 characters
- No wildcard imports

## Environment variables

```
GITHUB_TOKEN=ghp_...          # Required for GitHub API
ANTHROPIC_API_KEY=sk-ant-...  # Required for Claude Code adapter
OPENAI_API_KEY=sk-...         # Optional, for Codex adapter
GOOGLE_API_KEY=...            # Optional, for Gemini adapter
```

## How to run

```bash
# Install dependencies
uv sync

# Run the daemon (inherits from Delegate)
uv run delegate start

# Process a single issue (Phase 1)
uv run python -m reknew.main ReKnew-Data-and-AI/sp-enablers-slayer 114

# Run tests
uv run pytest tests/ -x -q
```

## Build order

Read and implement specs in this order:
1. `specs/phase1-issue-connector.md` — Issue connector + OpenSpec bridge + first run
2. `specs/phase2-llm-adapters.md` — LLM adapter interface + task breakdown + parallel agents
3. `specs/phase3-github-reactions.md` — GitHub PR integration + reactions engine
4. `specs/phase4-dashboard.md` — Capacity router + dashboard + executive view

Each spec file contains exact file paths, class signatures, method signatures, expected inputs/outputs, error handling requirements, and test cases.
