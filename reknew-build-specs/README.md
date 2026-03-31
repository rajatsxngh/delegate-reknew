# ReKnew AI-SDLC Platform

**Route unallocated backlog work to AI agent teams. Compress 2 weeks of work into 30 minutes.**

ReKnew is a capacity management layer for AI-powered software development. It connects to your GitHub or JIRA backlog, generates specs for each issue, breaks them into parallelizable tasks, routes unallocated work to AI coding agents, and delivers reviewed, tested, merge-ready PRs — autonomously.

Built on top of [Delegate](https://github.com/nikhilgarg28/delegate) for the orchestration engine. LLM-agnostic: works with Claude Code, OpenAI Codex, Gemini CLI, Amp, Aider, and local models.

## Quick start

```bash
# Prerequisites: Python 3.12+, Node.js 20+, Git 2.25+, Claude Code CLI

# Clone
git clone https://github.com/ReKnew-Data-and-AI/delegate-reknew.git
cd delegate-reknew

# Install
uv sync

# Configure
export GITHUB_TOKEN=ghp_your_token
export ANTHROPIC_API_KEY=sk-ant-your_key

# Edit reknew.yaml with your repos

# Process a single issue
reknew process ReKnew-Data-and-AI/sp-enablers-slayer 114

# Or start the full daemon with dashboard
reknew start
# Dashboard opens at http://localhost:3000
```

## How it works

1. **Issue enters** — from GitHub or JIRA
2. **Spec generated** — via OpenSpec (LLM-agnostic)
3. **Tasks broken down** — into a parallelizable graph
4. **Capacity routed** — allocated → human team, unallocated → AI agents
5. **Agents execute** — in isolated git worktrees, in parallel
6. **Code reviewed** — agent reviews agent (autonomous)
7. **PR created** — pushed to GitHub with auto-generated description
8. **CI monitored** — failures auto-fixed by re-prompting agents
9. **Merged** — sequential merge queue prevents conflicts

## Configuration

Everything is driven by `reknew.yaml`:

```yaml
projects:
  my-app:
    repo: owner/my-app
    default_branch: main

defaults:
  agent: claude-code
  max_parallel_agents: 5

reactions:
  ci-failed:
    auto: true
    retries: 2
  changes-requested:
    auto: true
  approved-and-green:
    auto: false
    action: notify

capacity:
  rules:
    - "label:critical -> human"
    - "label:documentation -> ai"
    - "default -> ai"
```

## Architecture

- **ReKnew layer** (`reknew/`): issue connector, OpenSpec bridge, capacity router, reactions engine, GitHub integration, dashboard
- **Delegate core** (`delegate/`): daemon, worktrees, merge worker, sandboxing, agent monitoring
- **LLM adapters**: swappable interface for any coding agent

## License

MIT (Delegate fork) + MIT (ReKnew additions)
