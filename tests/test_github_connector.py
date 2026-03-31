"""Tests for reknew.connectors.github_connector — mocked PyGithub."""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from reknew.connectors.github_connector import (
    GitHubConnector,
    IssueContext,
    MAX_FILE_TREE,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _mock_label(name):
    lbl = MagicMock()
    lbl.name = name
    return lbl


def _mock_comment(body):
    c = MagicMock()
    c.body = body
    return c


def _mock_assignee(login):
    a = MagicMock()
    a.login = login
    return a


def _mock_content_file(path, file_type="file", content=b"hello"):
    f = MagicMock()
    f.name = path.split("/")[-1]
    f.path = path
    f.type = file_type
    f.decoded_content = content
    return f


def _build_connector_and_mocks(
    issue_title="Test issue",
    issue_body="Some body",
    labels=None,
    comments=None,
    assignees=None,
    milestone=None,
    contents=None,
    readme=None,
):
    """Build a GitHubConnector with mocked Github internals."""
    with patch("reknew.connectors.github_connector.Github") as MockGithub:
        connector = GitHubConnector(token="fake-token")

        mock_repo = MagicMock()
        mock_repo.default_branch = "main"
        connector.gh.get_repo.return_value = mock_repo

        mock_issue = MagicMock()
        mock_issue.title = issue_title
        mock_issue.body = issue_body
        mock_issue.labels = [_mock_label(l) for l in (labels or [])]
        mock_issue.get_comments.return_value = [
            _mock_comment(c) for c in (comments or [])
        ]
        mock_issue.assignees = [_mock_assignee(a) for a in (assignees or [])]
        mock_issue.milestone = milestone
        mock_repo.get_issue.return_value = mock_issue

        if contents is not None:
            mock_repo.get_contents.side_effect = contents
        else:
            mock_repo.get_contents.return_value = []

        return connector, mock_repo


# ── Tests ─────────────────────────────────────────────────────────────────

def test_fetch_issue_basic():
    connector, mock_repo = _build_connector_and_mocks(
        issue_title="Add config metadata",
        issue_body="We need metadata",
    )
    ctx = connector.fetch_issue("owner/repo", 114)

    assert isinstance(ctx, IssueContext)
    assert ctx.issue_number == 114
    assert ctx.title == "Add config metadata"
    assert ctx.body == "We need metadata"
    assert ctx.repo_name == "owner/repo"
    assert ctx.default_branch == "main"


def test_fetch_issue_with_comments():
    connector, _ = _build_connector_and_mocks(
        comments=["first comment", "second comment", "third comment"],
    )
    ctx = connector.fetch_issue("owner/repo", 1)
    assert ctx.comments == ["first comment", "second comment", "third comment"]


def test_fetch_issue_with_labels():
    connector, _ = _build_connector_and_mocks(
        labels=["bug", "documentation"],
    )
    ctx = connector.fetch_issue("owner/repo", 1)
    assert ctx.labels == ["bug", "documentation"]


def test_file_tree_depth_limit():
    """Verify tree stops at depth 3."""
    connector, mock_repo = _build_connector_and_mocks()

    call_count = 0

    def mock_get_contents(path=""):
        nonlocal call_count
        call_count += 1
        if call_count > 20:
            return []
        d = _mock_content_file(f"{path}/sub" if path else "sub", "dir")
        return [d]

    mock_repo.get_contents.side_effect = mock_get_contents
    tree = connector._get_file_tree(mock_repo)
    # At most 4 dir levels: depth 0, 1, 2, 3
    assert len(tree) <= 5  # dir entries only


def test_file_tree_max_100():
    """Verify output capped at 100 entries."""
    connector, mock_repo = _build_connector_and_mocks()

    files = [
        _mock_content_file(f"file_{i}.py", "file") for i in range(150)
    ]
    mock_repo.get_contents.return_value = files
    tree = connector._get_file_tree(mock_repo)
    assert len(tree) == MAX_FILE_TREE


def test_file_tree_skips_hidden():
    """Verify .git, node_modules skipped."""
    connector, mock_repo = _build_connector_and_mocks()

    items = [
        _mock_content_file(".git", "dir"),
        _mock_content_file("node_modules", "dir"),
        _mock_content_file("__pycache__", "dir"),
        _mock_content_file("src", "dir"),
        _mock_content_file("README.md", "file"),
    ]
    mock_repo.get_contents.side_effect = lambda path="": (
        items if path == "" else []
    )
    tree = connector._get_file_tree(mock_repo)
    assert ".git/" not in tree
    assert "node_modules/" not in tree
    assert "__pycache__/" not in tree
    assert "src/" in tree
    assert "README.md" in tree


def test_find_related_files():
    """Body contains `src/main.py`, verify file fetched."""
    connector, mock_repo = _build_connector_and_mocks()

    mock_file = _mock_content_file("src/main.py", "file", b"print('hi')")
    mock_repo.get_contents.side_effect = lambda path="": mock_file

    result = connector._find_related_files(mock_repo, "Check `src/main.py`")
    assert len(result) == 1
    assert result[0]["path"] == "src/main.py"
    assert result[0]["content"] == "print('hi')"


def test_find_related_files_missing():
    """Referenced file doesn't exist, verify skipped."""
    connector, mock_repo = _build_connector_and_mocks()

    from github import GithubException
    mock_repo.get_contents.side_effect = GithubException(404, "Not Found", None)

    result = connector._find_related_files(mock_repo, "See `missing.py`")
    assert result == []


def test_find_linked_prs():
    """Body contains '#42 and #99', verify [42, 99]."""
    connector, _ = _build_connector_and_mocks()
    prs = connector._find_linked_prs("Fixes #42 and #99, see GH-7")
    assert 42 in prs
    assert 99 in prs
    assert 7 in prs


def test_format_for_openspec():
    """Verify output has all sections in correct order."""
    connector, _ = _build_connector_and_mocks()
    ctx = IssueContext(
        issue_number=114,
        title="Add config metadata",
        body="We need config metadata for the system.",
        labels=["enhancement", "config"],
        comments=["Looks good", "Agreed"],
        repo_name="owner/repo",
        default_branch="main",
        file_tree=["src/", "src/config.py", "README.md"],
        readme_content="# My Project\nA cool project.",
        related_files=[{"path": "src/config.py", "content": "x = 1"}],
        assignees=["alice"],
        milestone="v1.0",
        linked_prs=[10],
    )
    output = connector.format_for_openspec(ctx)
    assert "# Issue #114: Add config metadata" in output
    assert "## Description" in output
    assert "## Labels" in output
    assert "enhancement, config" in output
    assert "## Discussion" in output
    assert "Comment 1: Looks good" in output
    assert "## Repository: owner/repo" in output
    assert "## File structure" in output
    assert "## README (excerpt)" in output
    assert "## Related files" in output
    assert "### src/config.py" in output

    # Check order: Description before Labels before Discussion
    desc_pos = output.index("## Description")
    labels_pos = output.index("## Labels")
    disc_pos = output.index("## Discussion")
    assert desc_pos < labels_pos < disc_pos


def test_no_token():
    """Should raise ValueError when no token available."""
    with patch.dict("os.environ", {}, clear=True):
        # Also ensure GITHUB_TOKEN isn't set
        import os
        os.environ.pop("GITHUB_TOKEN", None)
        with patch("reknew.connectors.github_connector.Github"):
            with pytest.raises(ValueError, match="GITHUB_TOKEN"):
                GitHubConnector(token=None)


def test_rate_limit():
    """Mock rate limit, verify exception raised."""
    from github import RateLimitExceededException

    with patch("reknew.connectors.github_connector.Github") as MockGithub:
        connector = GitHubConnector(token="fake-token")
        connector.gh.get_repo.side_effect = RateLimitExceededException(
            403, {"message": "rate limit"}, None
        )
        mock_rl = MagicMock()
        mock_rl.core.reset = "2025-01-01T00:00:00Z"
        connector.gh.get_rate_limit.return_value = mock_rl

        with pytest.raises(RateLimitExceededException):
            connector.fetch_issue("owner/repo", 1)
