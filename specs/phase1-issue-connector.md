# Phase 1: Issue Connector + OpenSpec Bridge + First Run

## Goal
Take a real GitHub issue from sp-enablers-slayer, generate a spec via OpenSpec, spawn one Claude Code agent in a git worktree via Delegate, and produce code. This is the first end-to-end proof.

## Files to create (in order)

### 1. reknew/__init__.py
Empty file. Just makes reknew a package.

### 2. reknew/config.py

**Purpose:** Load and validate reknew.yaml. Every other component imports and uses this.

**Classes:**

```python
@dataclass
class ProjectConfig:
    repo: str                        # "owner/repo-name"
    default_branch: str = "main"
    test_command: str = ""
    path: str = ""                   # auto-populated local clone path

@dataclass
class ReactionRule:
    auto: bool = True
    action: str = "send-to-agent"
    retries: int = 2
    max_cost: float = 5.00
    escalate_after: str = "30m"

@dataclass
class CapacityConfig:
    allocated: str = "human"
    unallocated: str = "ai"
    rules: list[str] = field(default_factory=list)

@dataclass
class DefaultsConfig:
    agent: str = "claude-code"
    workspace: str = "worktree"
    spec_tool: str = "openspec"
    max_parallel_agents: int = 5
    agent_timeout: int = 3600
    stuck_timeout: int = 300
    review_enabled: bool = True

@dataclass
class GithubConfig:
    webhook_secret: str = ""
    auto_label_prs: bool = True
    pr_prefix: str = "[ReKnew]"
    poll_interval: int = 30
    mode: str = "poll"              # "poll" or "webhook"

@dataclass
class ReknewConfig:
    projects: dict[str, ProjectConfig]
    defaults: DefaultsConfig
    reactions: dict[str, ReactionRule]
    capacity: CapacityConfig
    github: GithubConfig
    port: int = 3000
```

**Functions:**

```python
def load_config(path: str = "reknew.yaml") -> ReknewConfig:
    """Load config from YAML file.

    Args:
        path: Path to reknew.yaml

    Returns:
        Validated ReknewConfig

    Raises:
        FileNotFoundError: if config file doesn't exist
        ValueError: if required fields are missing
        yaml.YAMLError: if YAML is malformed
    """
```

**Validation rules:**
- `projects` must have at least one entry
- Each project must have `repo` in "owner/name" format (contains exactly one "/")
- `defaults.agent` must be one of: "claude-code", "codex", "gemini", "amp", "aider", "local"
- `defaults.workspace` must be "worktree" or "clone"
- `reactions` keys must be from: "ci-failed", "changes-requested", "approved-and-green"
- `capacity.rules` entries must match pattern "field:value -> target" or "default -> target"
- `capacity.rules` targets must be "human" or "ai"

**Test file: tests/test_config.py**

Test cases:
- `test_load_valid_config`: Load the sample reknew.yaml, verify all fields parsed correctly
- `test_missing_config_file`: Should raise FileNotFoundError
- `test_empty_projects`: Should raise ValueError("At least one project required")
- `test_invalid_repo_format`: repo="noslash" should raise ValueError
- `test_invalid_agent`: agent="unknown" should raise ValueError
- `test_default_values`: Omitted optional fields should use defaults
- `test_capacity_rules_parsing`: Verify rules parse correctly
- `test_invalid_capacity_rule`: "badformat" should raise ValueError

---

### 3. reknew/connectors/__init__.py
Empty file.

### 4. reknew/connectors/github_connector.py

**Purpose:** Pull a GitHub issue with full repo context. This is Step 1 of the pipeline.

**Dependencies:** `PyGithub`

**Classes:**

```python
@dataclass
class IssueContext:
    issue_number: int
    title: str
    body: str
    labels: list[str]
    comments: list[str]
    repo_name: str
    default_branch: str
    file_tree: list[str]          # all file paths in repo (max 100)
    readme_content: str            # README.md content (max 3000 chars)
    related_files: list[dict]      # {path: str, content: str} for files mentioned in body
    assignees: list[str]
    milestone: str
    linked_prs: list[int]          # PR numbers referenced in issue

class GitHubConnector:
    def __init__(self, token: str | None = None):
        """Initialize with token from param or GITHUB_TOKEN env var.
        Raises ValueError if no token available."""

    def fetch_issue(self, repo_name: str, issue_number: int) -> IssueContext:
        """Pull issue with full context.

        Args:
            repo_name: "owner/repo" format
            issue_number: GitHub issue number

        Returns:
            IssueContext with all fields populated

        Raises:
            github.GithubException: if repo or issue not found
            github.RateLimitExceededException: if API rate limit hit
        """

    def _get_file_tree(self, repo, path: str = "", depth: int = 0, max_depth: int = 3) -> list[str]:
        """Recursively get repo file tree.

        Returns list of file paths, directories suffixed with "/".
        Stops at max_depth=3 to avoid API rate limits.
        Limits output to 100 entries.
        Skips: .git/, node_modules/, __pycache__/, .venv/, .env
        """

    def _find_related_files(self, repo, body: str) -> list[dict]:
        """Find files mentioned in backticks in issue body.

        Scans body for patterns like `path/to/file.py`.
        Fetches each file's content (truncated to 2000 chars).
        Returns list of {path: str, content: str}.
        Silently skips files that don't exist.
        """

    def _find_linked_prs(self, body: str) -> list[int]:
        """Find PR numbers referenced in issue body.

        Matches patterns: #123, GH-123, owner/repo#123
        Returns list of PR numbers.
        """

    def format_for_openspec(self, ctx: IssueContext) -> str:
        """Format IssueContext as markdown for OpenSpec consumption.

        Output format:
        # Issue #{number}: {title}
        ## Description
        {body}
        ## Labels
        {comma-separated labels}
        ## Discussion
        Comment 1: {text}
        Comment 2: {text}
        ## Repository: {repo_name}
        ## File structure
        ```
        {file_tree, max 100 entries}
        ```
        ## README (excerpt)
        {readme, max 3000 chars}
        ## Related files
        ### {path}
        ```
        {content, max 2000 chars}
        ```
        """
```

**Error handling:**
- If GITHUB_TOKEN is not set: raise ValueError("GITHUB_TOKEN environment variable required")
- If repo not found: let PyGithub's exception propagate with clear message
- If rate limited: catch RateLimitExceededException, log the reset time, raise
- If file_tree API calls fail for a subdirectory: skip silently, continue

**Test file: tests/test_github_connector.py**

Test cases (use unittest.mock to mock PyGithub):
- `test_fetch_issue_basic`: Mock a basic issue, verify all fields in IssueContext
- `test_fetch_issue_with_comments`: Mock issue with 3 comments, verify comments list
- `test_fetch_issue_with_labels`: Mock issue with labels ["bug", "documentation"]
- `test_file_tree_depth_limit`: Verify tree stops at depth 3
- `test_file_tree_max_100`: Verify output capped at 100 entries
- `test_file_tree_skips_hidden`: Verify .git, node_modules skipped
- `test_find_related_files`: Body contains "`src/main.py`", verify file fetched
- `test_find_related_files_missing`: Referenced file doesn't exist, verify skipped
- `test_find_linked_prs`: Body contains "#42 and #99", verify [42, 99]
- `test_format_for_openspec`: Verify output has all sections in correct order
- `test_no_token`: Should raise ValueError
- `test_rate_limit`: Mock rate limit, verify exception raised with reset time

---

### 5. reknew/spec/__init__.py
Empty file.

### 6. reknew/spec/openspec_bridge.py

**Purpose:** Connect issue context to OpenSpec's spec generation workflow.

**Important:** OpenSpec is a CLI tool. The bridge does NOT call OpenSpec directly. It prepares context files and generates a prompt that the coding agent will execute using OpenSpec's slash commands.

```python
class OpenSpecBridge:
    def __init__(self, repo_path: str):
        """Initialize with path to the repo clone."""
        self.repo_path = Path(repo_path)

    def ensure_initialized(self) -> None:
        """Run 'openspec init' if openspec/ dir doesn't exist.

        Raises:
            FileNotFoundError: if openspec CLI not installed
            subprocess.CalledProcessError: if init fails
        """

    def create_change_proposal(self, issue_context_text: str, change_name: str) -> Path:
        """Create the change directory and write issue context.

        Creates: openspec/changes/{change_name}/issue_context.md
        Returns: Path to the change directory

        Args:
            issue_context_text: formatted markdown from GitHubConnector.format_for_openspec()
            change_name: slug like "issue-114"
        """

    def get_agent_prompt(self, change_name: str) -> str:
        """Generate the prompt for the coding agent.

        The prompt instructs the agent to:
        1. Read the issue context file
        2. Run /opsx:new {change_name} to create the proposal
        3. Run /opsx:ff to fast-forward through planning
        4. Run /opsx:apply {change_name} to implement
        5. Run tests if available

        Returns: complete prompt string
        """

    def get_spec_output_path(self, change_name: str) -> Path:
        """Return path to the generated spec file.

        Returns: openspec/changes/{change_name}/specs/ directory
        """
```

**Test file: tests/test_openspec_bridge.py**

Test cases (use tmp_path fixture for temp directories):
- `test_ensure_initialized_creates_dir`: Verify openspec/ created
- `test_ensure_initialized_skips_existing`: Don't re-init if exists
- `test_create_change_proposal`: Verify file written at correct path
- `test_get_agent_prompt_contains_commands`: Verify /opsx:new, /opsx:ff, /opsx:apply in prompt
- `test_change_name_sanitization`: "Issue #114!" should work (slugified)

---

### 7. reknew/main.py

**Purpose:** Entry point for Phase 1. Processes a single issue end-to-end.

```python
async def run_single_issue(repo_name: str, issue_number: int) -> None:
    """Process one issue through the full Phase 1 pipeline.

    Steps:
    1. Load config from reknew.yaml
    2. Fetch issue via GitHubConnector
    3. Format for OpenSpec
    4. Clone repo if not already cloned (to ~/.reknew/repos/{repo-name}/)
    5. Initialize OpenSpec in the clone
    6. Create change proposal
    7. Create a git worktree via subprocess
       - Branch: reknew/task/T{issue_number:04d}
       - Path: ~/.reknew/worktrees/T{issue_number:04d}/
    8. Copy the openspec change proposal into the worktree
    9. Spawn Claude Code in the worktree with the agent prompt
       - Command: claude --print --dangerously-skip-permissions "{prompt}"
       - Working directory: worktree path
       - Stdout/stderr: ~/.reknew/logs/T{issue_number:04d}.log
    10. Poll process every 5 seconds until done
    11. Print results: success/fail, worktree path, log path

    Output to terminal:
    [1/5] Fetching issue #114 from ReKnew-Data-and-AI/sp-enablers-slayer...
          Title: Add configuration metadata
          Labels: enhancement
          Files in repo: 47
    [2/5] Generating spec via OpenSpec...
    [3/5] Creating isolated worktree...
          Worktree: ~/.reknew/worktrees/T0114/
          Branch: reknew/task/T0114
    [4/5] Spawning Claude Code agent...
    [5/5] Agent working... (logs: ~/.reknew/logs/T0114.log)
          Still working...
          Still working...
    ✓ Agent completed successfully!
      Check changes: cd ~/.reknew/worktrees/T0114 && git diff
      View logs: cat ~/.reknew/logs/T0114.log
    """

def main():
    """CLI entry point. Parse args and run.

    Usage: python -m reknew.main <repo> <issue_number>
    Example: python -m reknew.main ReKnew-Data-and-AI/sp-enablers-slayer 114
    """
```

**Error handling:**
- Missing GITHUB_TOKEN: print helpful message and exit
- Missing ANTHROPIC_API_KEY: print helpful message and exit
- Claude Code not installed: check with `which claude`, print install instructions
- OpenSpec not installed: check with `which openspec`, print install instructions
- Repo clone fails: print git error and exit
- Agent crashes (non-zero exit): print log file path and last 20 lines of log

**Directories created automatically:**
- `~/.reknew/repos/` — repo clones
- `~/.reknew/worktrees/` — git worktrees
- `~/.reknew/logs/` — agent log files

---

## End-to-end test (manual)

After building all Phase 1 files, verify with:

```bash
export GITHUB_TOKEN=ghp_your_token
export ANTHROPIC_API_KEY=sk-ant-your_key

# Run against a real issue
python -m reknew.main ReKnew-Data-and-AI/sp-enablers-slayer 114

# Check the output
cd ~/.reknew/worktrees/T0114
git log --oneline
git diff HEAD~1
```

Expected: the worktree contains new/modified files addressing issue #114 (Add configuration metadata).

## Definition of done

- [ ] `reknew.yaml` exists and is valid
- [ ] `reknew/config.py` loads and validates config
- [ ] `reknew/connectors/github_connector.py` fetches issues with full context
- [ ] `reknew/spec/openspec_bridge.py` creates change proposals and generates prompts
- [ ] `reknew/main.py` runs end-to-end: issue → spec → worktree → agent → code
- [ ] All tests pass: `pytest tests/test_config.py tests/test_github_connector.py tests/test_openspec_bridge.py -v`
- [ ] Successfully processed at least one real SLayer issue
