"""Tests for reknew.config — YAML config loader and validator."""

import pytest
import yaml

from reknew.config import load_config, ReknewConfig


@pytest.fixture
def valid_yaml(tmp_path):
    """Write the real reknew.yaml and return its path."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "reknew.yaml"
    return str(src)


@pytest.fixture
def _write_yaml(tmp_path):
    """Helper: write arbitrary YAML dict to a temp file and return path."""
    def _inner(data: dict) -> str:
        p = tmp_path / "reknew.yaml"
        p.write_text(yaml.dump(data))
        return str(p)
    return _inner


# ── Happy path ────────────────────────────────────────────────────────────

def test_load_valid_config(valid_yaml):
    cfg = load_config(valid_yaml)
    assert isinstance(cfg, ReknewConfig)
    assert "slayer" in cfg.projects
    assert cfg.projects["slayer"].repo == "ReKnew-Data-and-AI/sp-enablers-slayer"
    assert cfg.defaults.agent == "claude-code"
    assert cfg.defaults.workspace == "worktree"
    assert cfg.defaults.max_parallel_agents == 5
    assert cfg.port == 3000
    assert "ci-failed" in cfg.reactions
    assert cfg.reactions["ci-failed"].retries == 2
    assert cfg.capacity.allocated == "human"
    assert cfg.github.mode == "poll"


def test_default_values(_write_yaml):
    data = {
        "projects": {"p": {"repo": "owner/name"}},
    }
    cfg = load_config(_write_yaml(data))
    assert cfg.defaults.agent == "claude-code"
    assert cfg.defaults.workspace == "worktree"
    assert cfg.defaults.max_parallel_agents == 5
    assert cfg.defaults.agent_timeout == 3600
    assert cfg.defaults.stuck_timeout == 300
    assert cfg.defaults.review_enabled is True
    assert cfg.port == 3000
    assert cfg.github.poll_interval == 30


def test_capacity_rules_parsing(valid_yaml):
    cfg = load_config(valid_yaml)
    assert len(cfg.capacity.rules) > 0
    assert "label:critical -> human" in cfg.capacity.rules
    assert "default -> ai" in cfg.capacity.rules


# ── Error paths ───────────────────────────────────────────────────────────

def test_missing_config_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/reknew.yaml")


def test_empty_projects(_write_yaml):
    data = {"projects": {}}
    with pytest.raises(ValueError, match="At least one project required"):
        load_config(_write_yaml(data))


def test_invalid_repo_format(_write_yaml):
    data = {"projects": {"bad": {"repo": "noslash"}}}
    with pytest.raises(ValueError, match="Invalid repo format"):
        load_config(_write_yaml(data))


def test_invalid_agent(_write_yaml):
    data = {
        "projects": {"p": {"repo": "owner/name"}},
        "defaults": {"agent": "unknown"},
    }
    with pytest.raises(ValueError, match="Invalid agent"):
        load_config(_write_yaml(data))


def test_invalid_capacity_rule(_write_yaml):
    data = {
        "projects": {"p": {"repo": "owner/name"}},
        "capacity": {"rules": ["badformat"]},
    }
    with pytest.raises(ValueError, match="Invalid capacity rule"):
        load_config(_write_yaml(data))
