"""Load and validate reknew.yaml configuration."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


VALID_AGENTS = {"claude-code", "codex", "gemini", "amp", "aider", "local"}
VALID_WORKSPACES = {"worktree", "clone"}
VALID_REACTION_KEYS = {"ci-failed", "changes-requested", "approved-and-green"}
CAPACITY_RULE_RE = re.compile(
    r"^(?:(\w[\w.]*):([^\s]+)|default)\s*->\s*(human|ai)$"
)


@dataclass
class ProjectConfig:
    """Configuration for a single project/repository."""

    repo: str
    default_branch: str = "main"
    test_command: str = ""
    path: str = ""


@dataclass
class ReactionRule:
    """Configuration for a single reaction rule."""

    auto: bool = True
    action: str = "send-to-agent"
    retries: int = 2
    max_cost: float = 5.00
    escalate_after: str = "30m"


@dataclass
class CapacityConfig:
    """Configuration for capacity management routing."""

    allocated: str = "human"
    unallocated: str = "ai"
    rules: list[str] = field(default_factory=list)


@dataclass
class DefaultsConfig:
    """Default settings for agent behaviour."""

    agent: str = "claude-code"
    workspace: str = "worktree"
    spec_tool: str = "openspec"
    max_parallel_agents: int = 5
    agent_timeout: int = 3600
    stuck_timeout: int = 300
    review_enabled: bool = True


@dataclass
class GithubConfig:
    """GitHub integration settings."""

    webhook_secret: str = ""
    auto_label_prs: bool = True
    pr_prefix: str = "[ReKnew]"
    poll_interval: int = 30
    mode: str = "poll"


@dataclass
class ReknewConfig:
    """Top-level configuration container."""

    projects: dict[str, ProjectConfig]
    defaults: DefaultsConfig
    reactions: dict[str, ReactionRule]
    capacity: CapacityConfig
    github: GithubConfig
    port: int = 3000


def _validate_repo(repo: str) -> None:
    """Ensure repo is in 'owner/name' format."""
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid repo format '{repo}': must be 'owner/name'"
        )


def _validate_capacity_rules(rules: list[str]) -> None:
    """Ensure every capacity rule matches the expected pattern."""
    for rule in rules:
        if not CAPACITY_RULE_RE.match(rule):
            raise ValueError(
                f"Invalid capacity rule '{rule}': must match "
                "'field:value -> human|ai' or 'default -> human|ai'"
            )


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
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Config file is empty")

    # --- Projects ---
    raw_projects = raw.get("projects", {})
    if not raw_projects:
        raise ValueError("At least one project required")

    projects: dict[str, ProjectConfig] = {}
    for name, proj_data in raw_projects.items():
        if not isinstance(proj_data, dict):
            raise ValueError(f"Project '{name}' must be a mapping")
        repo = proj_data.get("repo", "")
        _validate_repo(repo)
        projects[name] = ProjectConfig(
            repo=repo,
            default_branch=proj_data.get("default_branch", "main"),
            test_command=proj_data.get("test_command", ""),
            path=proj_data.get("path", ""),
        )

    # --- Defaults ---
    raw_defaults = raw.get("defaults", {})
    if not isinstance(raw_defaults, dict):
        raw_defaults = {}

    agent = raw_defaults.get("agent", "claude-code")
    if agent not in VALID_AGENTS:
        raise ValueError(
            f"Invalid agent '{agent}': must be one of {sorted(VALID_AGENTS)}"
        )

    workspace = raw_defaults.get("workspace", "worktree")
    if workspace not in VALID_WORKSPACES:
        raise ValueError(
            f"Invalid workspace '{workspace}': must be 'worktree' or 'clone'"
        )

    defaults = DefaultsConfig(
        agent=agent,
        workspace=workspace,
        spec_tool=raw_defaults.get("spec_tool", "openspec"),
        max_parallel_agents=raw_defaults.get("max_parallel_agents", 5),
        agent_timeout=raw_defaults.get("agent_timeout", 3600),
        stuck_timeout=raw_defaults.get("stuck_timeout", 300),
        review_enabled=raw_defaults.get("review_enabled", True),
    )

    # --- Reactions ---
    raw_reactions = raw.get("reactions", {})
    if not isinstance(raw_reactions, dict):
        raw_reactions = {}

    reactions: dict[str, ReactionRule] = {}
    for key, rule_data in raw_reactions.items():
        if key not in VALID_REACTION_KEYS:
            raise ValueError(
                f"Invalid reaction key '{key}': "
                f"must be one of {sorted(VALID_REACTION_KEYS)}"
            )
        if not isinstance(rule_data, dict):
            rule_data = {}
        reactions[key] = ReactionRule(
            auto=rule_data.get("auto", True),
            action=rule_data.get("action", "send-to-agent"),
            retries=rule_data.get("retries", 2),
            max_cost=float(rule_data.get("max_cost", 5.00)),
            escalate_after=rule_data.get("escalate_after", "30m"),
        )

    # --- Capacity ---
    raw_capacity = raw.get("capacity", {})
    if not isinstance(raw_capacity, dict):
        raw_capacity = {}

    capacity_rules = raw_capacity.get("rules", [])
    _validate_capacity_rules(capacity_rules)

    capacity = CapacityConfig(
        allocated=raw_capacity.get("allocated", "human"),
        unallocated=raw_capacity.get("unallocated", "ai"),
        rules=capacity_rules,
    )

    # --- GitHub ---
    raw_github = raw.get("github", {})
    if not isinstance(raw_github, dict):
        raw_github = {}

    github = GithubConfig(
        webhook_secret=raw_github.get("webhook_secret", ""),
        auto_label_prs=raw_github.get("auto_label_prs", True),
        pr_prefix=raw_github.get("pr_prefix", "[ReKnew]"),
        poll_interval=raw_github.get("poll_interval", 30),
        mode=raw_github.get("mode", "poll"),
    )

    return ReknewConfig(
        projects=projects,
        defaults=defaults,
        reactions=reactions,
        capacity=capacity,
        github=github,
        port=raw.get("port", 3000),
    )
