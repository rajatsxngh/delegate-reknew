# GitHub Copilot Instructions — ReKnew AI-SDLC Platform

This project extends Delegate (an AI agent orchestrator). All ReKnew code is in `reknew/`. Do not modify files in `delegate/` unless explicitly instructed.

## Key patterns

- Config-driven: read settings from `reknew.yaml` via `reknew/config.py`
- Adapter pattern: LLM adapters implement `AgentAdapter` ABC from `reknew/adapters/base_adapter.py`
- State machine: tasks flow through `TaskState` enum in `reknew/orchestration/task_state.py`
- Event-driven: components emit events via `EventBroadcaster` in `reknew/dashboard/websocket.py`
- Daemon-only git: only daemon code runs git worktree/branch/merge/push commands

## Code style

- Python 3.12+, type hints, Google docstrings
- dataclasses for data containers, Enum for states
- async/await for I/O in daemon, pytest-asyncio for tests
- subprocess.run for shell commands, pathlib.Path for file paths
- Mock external APIs in tests (PyGithub, Anthropic SDK)
