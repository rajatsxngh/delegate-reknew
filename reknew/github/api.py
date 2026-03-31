"""Centralized GitHub API access."""

import logging
import os

from github import Github, GithubException

logger = logging.getLogger(__name__)


class GitHubAPI:
    """Centralized GitHub API access.

    All GitHub operations go through this class. It handles:
    - Authentication (token from env or config)
    - Rate limit tracking and warnings
    - Consistent error handling
    """

    def __init__(self, token: str | None = None):
        """Initialize. Token from param, config, or GITHUB_TOKEN env var.

        Raises:
            ValueError: if no token available.
        """
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN environment variable required")
        self.gh = Github(self.token)

    @property
    def rate_limit_remaining(self) -> int:
        """Current remaining API requests before rate limit."""
        return self.gh.get_rate_limit().core.remaining

    def get_repo(self, repo_name: str):
        """Get a PyGithub Repository object.

        Args:
            repo_name: "owner/repo" format

        Returns:
            github.Repository.Repository object

        Raises:
            GithubException: if repo not found
        """
        return self.gh.get_repo(repo_name)

    def get_issue(self, repo_name: str, issue_number: int):
        """Get a PyGithub Issue object.

        Args:
            repo_name: "owner/repo" format
            issue_number: GitHub issue number

        Returns:
            github.Issue.Issue object
        """
        repo = self.get_repo(repo_name)
        return repo.get_issue(number=issue_number)

    def get_pull(self, repo_name: str, pr_number: int):
        """Get a PyGithub PullRequest object.

        Args:
            repo_name: "owner/repo" format
            pr_number: GitHub PR number

        Returns:
            github.PullRequest.PullRequest object
        """
        repo = self.get_repo(repo_name)
        return repo.get_pull(number=pr_number)
