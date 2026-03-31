"""OpenSpec bridge — prepares context and prompts for spec-driven agents."""

import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(name: str) -> str:
    """Convert a name like 'Issue #114!' to 'issue-114'."""
    return SLUG_RE.sub("-", name.lower()).strip("-")


class OpenSpecBridge:
    """Connects issue context to OpenSpec's spec generation workflow.

    OpenSpec is a CLI tool. The bridge does NOT call OpenSpec directly.
    It prepares context files and generates a prompt that the coding agent
    will execute using OpenSpec's slash commands.
    """

    def __init__(self, repo_path: str):
        """Initialize with path to the repo clone.

        Args:
            repo_path: Absolute path to the repository clone.
        """
        self.repo_path = Path(repo_path)

    def ensure_initialized(self) -> None:
        """Run 'openspec init' if openspec/ dir doesn't exist.

        Raises:
            FileNotFoundError: if openspec CLI not installed
            subprocess.CalledProcessError: if init fails
        """
        openspec_dir = self.repo_path / "openspec"
        if openspec_dir.exists():
            logger.info("openspec/ already exists, skipping init")
            return

        if not shutil.which("openspec"):
            raise FileNotFoundError(
                "openspec CLI not found. Install it first: "
                "pip install openspec-cli"
            )

        logger.info("Running openspec init in %s", self.repo_path)
        subprocess.run(
            ["openspec", "init"],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )

    def create_change_proposal(
        self, issue_context_text: str, change_name: str
    ) -> Path:
        """Create the change directory and write issue context.

        Creates: openspec/changes/{change_name}/issue_context.md
        Returns: Path to the change directory

        Args:
            issue_context_text: Formatted markdown from
                GitHubConnector.format_for_openspec()
            change_name: Slug like 'issue-114'
        """
        slug = _slugify(change_name)
        change_dir = self.repo_path / "openspec" / "changes" / slug
        change_dir.mkdir(parents=True, exist_ok=True)

        context_file = change_dir / "issue_context.md"
        context_file.write_text(issue_context_text, encoding="utf-8")
        logger.info("Wrote issue context to %s", context_file)

        return change_dir

    def get_agent_prompt(self, change_name: str) -> str:
        """Generate the prompt for the coding agent.

        The prompt instructs the agent to:
        1. Read the issue context file
        2. Run /opsx:new {change_name} to create the proposal
        3. Run /opsx:ff to fast-forward through planning
        4. Run /opsx:apply {change_name} to implement
        5. Run tests if available

        Args:
            change_name: Slug for the change (e.g. 'issue-114')

        Returns:
            Complete prompt string.
        """
        slug = _slugify(change_name)
        context_path = (
            f"openspec/changes/{slug}/issue_context.md"
        )

        return f"""\
You are implementing a code change based on a GitHub issue.

Step 1: Read the issue context file at `{context_path}`.

Step 2: Run /opsx:new {slug} to create a new change proposal.

Step 3: Run /opsx:ff to fast-forward through the planning phase.

Step 4: Run /opsx:apply {slug} to implement the changes.

Step 5: Run the project's test suite to verify the changes work correctly.

Focus on producing clean, working code that addresses the issue requirements.
Do not modify unrelated files. Keep changes minimal and focused."""

    def get_spec_output_path(self, change_name: str) -> Path:
        """Return path to the generated spec file.

        Args:
            change_name: Slug for the change

        Returns:
            Path to openspec/changes/{change_name}/specs/ directory
        """
        slug = _slugify(change_name)
        return self.repo_path / "openspec" / "changes" / slug / "specs"
