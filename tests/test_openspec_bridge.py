"""Tests for reknew.spec.openspec_bridge — OpenSpec integration layer."""

from unittest.mock import patch

import pytest

from reknew.spec.openspec_bridge import OpenSpecBridge, _slugify


def test_ensure_initialized_creates_dir(tmp_path):
    """Verify openspec init is called when openspec/ doesn't exist."""
    bridge = OpenSpecBridge(str(tmp_path))

    with patch("reknew.spec.openspec_bridge.shutil.which", return_value="/usr/bin/openspec"):
        with patch("reknew.spec.openspec_bridge.subprocess.run") as mock_run:
            bridge.ensure_initialized()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["openspec", "init"]


def test_ensure_initialized_skips_existing(tmp_path):
    """Don't re-init if openspec/ already exists."""
    (tmp_path / "openspec").mkdir()
    bridge = OpenSpecBridge(str(tmp_path))

    with patch("reknew.spec.openspec_bridge.subprocess.run") as mock_run:
        bridge.ensure_initialized()
        mock_run.assert_not_called()


def test_ensure_initialized_missing_cli(tmp_path):
    """Raise FileNotFoundError if openspec CLI not installed."""
    bridge = OpenSpecBridge(str(tmp_path))

    with patch("reknew.spec.openspec_bridge.shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="openspec CLI not found"):
            bridge.ensure_initialized()


def test_create_change_proposal(tmp_path):
    """Verify file written at correct path."""
    bridge = OpenSpecBridge(str(tmp_path))
    context_text = "# Issue #114: Add config metadata"

    change_dir = bridge.create_change_proposal(context_text, "issue-114")

    assert change_dir.exists()
    context_file = change_dir / "issue_context.md"
    assert context_file.exists()
    assert context_file.read_text() == context_text
    assert "issue-114" in str(change_dir)


def test_get_agent_prompt_contains_commands(tmp_path):
    """Verify /opsx:new, /opsx:ff, /opsx:apply in prompt."""
    bridge = OpenSpecBridge(str(tmp_path))
    prompt = bridge.get_agent_prompt("issue-114")

    assert "/opsx:new issue-114" in prompt
    assert "/opsx:ff" in prompt
    assert "/opsx:apply issue-114" in prompt
    assert "issue_context.md" in prompt


def test_change_name_sanitization(tmp_path):
    """'Issue #114!' should work (slugified)."""
    bridge = OpenSpecBridge(str(tmp_path))

    # create_change_proposal should slugify
    change_dir = bridge.create_change_proposal("context", "Issue #114!")
    assert "issue-114" in str(change_dir)

    # get_agent_prompt should also slugify
    prompt = bridge.get_agent_prompt("Issue #114!")
    assert "/opsx:new issue-114" in prompt

    # get_spec_output_path should also slugify
    spec_path = bridge.get_spec_output_path("Issue #114!")
    assert "issue-114" in str(spec_path)


def test_get_spec_output_path(tmp_path):
    """Verify spec output path structure."""
    bridge = OpenSpecBridge(str(tmp_path))
    path = bridge.get_spec_output_path("issue-42")
    assert path == tmp_path / "openspec" / "changes" / "issue-42" / "specs"


def test_slugify():
    assert _slugify("issue-114") == "issue-114"
    assert _slugify("Issue #114!") == "issue-114"
    assert _slugify("My Cool Feature") == "my-cool-feature"
    assert _slugify("  spaces  ") == "spaces"
