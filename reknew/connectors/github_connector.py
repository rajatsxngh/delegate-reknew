"""GitHub issue connector — fetches issues with full repo context."""

import logging
import os
import re
from dataclasses import dataclass, field

from github import Github, GithubException, RateLimitExceededException

logger = logging.getLogger(__name__)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".env"}
MAX_FILE_TREE = 100
MAX_README_CHARS = 3000
MAX_FILE_CONTENT_CHARS = 2000
BACKTICK_FILE_RE = re.compile(r"`([a-zA-Z0-9_/.@-]+\.[a-zA-Z0-9]+)`")
LINKED_PR_RE = re.compile(r"(?:GH-|#)(\d+)")


@dataclass
class IssueContext:
    """Full context for a GitHub issue."""

    issue_number: int
    title: str
    body: str
    labels: list[str]
    comments: list[str]
    repo_name: str
    default_branch: str
    file_tree: list[str]
    readme_content: str
    related_files: list[dict] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    milestone: str = ""
    linked_prs: list[int] = field(default_factory=list)


class GitHubConnector:
    """Connects to GitHub to fetch issue context for spec generation."""

    def __init__(self, token: str | None = None):
        """Initialize with token from param or GITHUB_TOKEN env var.

        Raises:
            ValueError: if no token available.
        """
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN environment variable required")
        self.gh = Github(self.token)

    def fetch_issue(self, repo_name: str, issue_number: int) -> IssueContext:
        """Pull issue with full context.

        Args:
            repo_name: "owner/repo" format
            issue_number: GitHub issue number

        Returns:
            IssueContext with all fields populated

        Raises:
            GithubException: if repo or issue not found
            RateLimitExceededException: if API rate limit hit
        """
        try:
            repo = self.gh.get_repo(repo_name)
        except RateLimitExceededException as exc:
            rl = self.gh.get_rate_limit().core
            logger.error("Rate limited. Resets at %s", rl.reset)
            raise
        except GithubException:
            raise

        try:
            issue = repo.get_issue(number=issue_number)
        except RateLimitExceededException as exc:
            rl = self.gh.get_rate_limit().core
            logger.error("Rate limited. Resets at %s", rl.reset)
            raise

        labels = [lbl.name for lbl in issue.labels]
        comments = [c.body for c in issue.get_comments()]
        assignees = [a.login for a in issue.assignees]
        milestone = issue.milestone.title if issue.milestone else ""
        body = issue.body or ""

        file_tree = self._get_file_tree(repo)
        readme_content = self._get_readme(repo)
        related_files = self._find_related_files(repo, body)
        linked_prs = self._find_linked_prs(body)

        return IssueContext(
            issue_number=issue_number,
            title=issue.title,
            body=body,
            labels=labels,
            comments=comments,
            repo_name=repo_name,
            default_branch=repo.default_branch,
            file_tree=file_tree,
            readme_content=readme_content,
            related_files=related_files,
            assignees=assignees,
            milestone=milestone,
            linked_prs=linked_prs,
        )

    def _get_file_tree(
        self, repo, path: str = "", depth: int = 0, max_depth: int = 3
    ) -> list[str]:
        """Recursively get repo file tree.

        Returns list of file paths, directories suffixed with '/'.
        Stops at max_depth=3 to avoid API rate limits.
        Limits output to 100 entries.
        Skips: .git/, node_modules/, __pycache__/, .venv/, .env
        """
        if depth > max_depth:
            return []

        results: list[str] = []
        try:
            contents = repo.get_contents(path)
        except (GithubException, Exception):
            return results

        if not isinstance(contents, list):
            contents = [contents]

        for item in contents:
            if len(results) >= MAX_FILE_TREE:
                break
            if item.name in SKIP_DIRS:
                continue
            if item.type == "dir":
                results.append(item.path + "/")
                if len(results) < MAX_FILE_TREE:
                    sub = self._get_file_tree(
                        repo, item.path, depth + 1, max_depth
                    )
                    results.extend(sub[:MAX_FILE_TREE - len(results)])
            else:
                results.append(item.path)

        return results[:MAX_FILE_TREE]

    def _get_readme(self, repo) -> str:
        """Fetch README.md content, truncated to MAX_README_CHARS."""
        try:
            readme = repo.get_contents("README.md")
            content = readme.decoded_content.decode("utf-8", errors="replace")
            return content[:MAX_README_CHARS]
        except (GithubException, Exception):
            return ""

    def _find_related_files(self, repo, body: str) -> list[dict]:
        """Find files mentioned in backticks in issue body.

        Scans body for patterns like `path/to/file.py`.
        Fetches each file's content (truncated to 2000 chars).
        Returns list of {path: str, content: str}.
        Silently skips files that don't exist.
        """
        matches = BACKTICK_FILE_RE.findall(body)
        results: list[dict] = []
        seen: set[str] = set()
        for file_path in matches:
            if file_path in seen:
                continue
            seen.add(file_path)
            try:
                content_file = repo.get_contents(file_path)
                if content_file.type != "file":
                    continue
                content = content_file.decoded_content.decode(
                    "utf-8", errors="replace"
                )
                results.append({
                    "path": file_path,
                    "content": content[:MAX_FILE_CONTENT_CHARS],
                })
            except (GithubException, Exception):
                continue
        return results

    def _find_linked_prs(self, body: str) -> list[int]:
        """Find PR numbers referenced in issue body.

        Matches patterns: #123, GH-123, owner/repo#123
        Returns list of PR numbers.
        """
        matches = LINKED_PR_RE.findall(body)
        return list(dict.fromkeys(int(m) for m in matches))

    def format_for_openspec(self, ctx: IssueContext) -> str:
        """Format IssueContext as markdown for OpenSpec consumption.

        Args:
            ctx: Populated IssueContext

        Returns:
            Markdown string with all sections.
        """
        parts: list[str] = []
        parts.append(f"# Issue #{ctx.issue_number}: {ctx.title}")

        parts.append("\n## Description")
        parts.append(ctx.body or "(no description)")

        if ctx.labels:
            parts.append("\n## Labels")
            parts.append(", ".join(ctx.labels))

        if ctx.comments:
            parts.append("\n## Discussion")
            for i, comment in enumerate(ctx.comments, 1):
                parts.append(f"Comment {i}: {comment}")

        parts.append(f"\n## Repository: {ctx.repo_name}")

        if ctx.file_tree:
            parts.append("\n## File structure")
            parts.append("```")
            for entry in ctx.file_tree[:MAX_FILE_TREE]:
                parts.append(entry)
            parts.append("```")

        if ctx.readme_content:
            parts.append("\n## README (excerpt)")
            parts.append(ctx.readme_content[:MAX_README_CHARS])

        if ctx.related_files:
            parts.append("\n## Related files")
            for rf in ctx.related_files:
                parts.append(f"\n### {rf['path']}")
                parts.append("```")
                parts.append(rf["content"][:MAX_FILE_CONTENT_CHARS])
                parts.append("```")

        return "\n".join(parts)
