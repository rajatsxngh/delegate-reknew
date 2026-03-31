"""Specialized handler for code review comment processing."""

from collections import defaultdict

from reknew.github.pr_manager import ReviewComment


class ReviewHandler:
    """Processes code review comments into agent-friendly prompts."""

    def format_for_agent(self, comments: list[ReviewComment]) -> str:
        """Format review comments as a prompt.

        Groups comments by file. For inline comments, includes
        the file path and line number so the agent knows exactly
        where to look.

        Args:
            comments: list of ReviewComment objects

        Returns:
            Formatted prompt string
        """
        ordered = self.prioritize_comments(comments)

        # Group by file (None for general comments)
        by_file: dict[str | None, list[ReviewComment]] = defaultdict(list)
        for c in ordered:
            by_file[c.path].append(c)

        parts = [
            "Code review feedback. Please address the following:\n"
        ]

        # General comments first
        general = by_file.pop(None, [])
        if general:
            parts.append("## General comments")
            for c in general:
                parts.append(f"- [{c.reviewer}]: {c.body}")
            parts.append("")

        # File-specific comments
        for path, file_comments in sorted(
            by_file.items(), key=lambda x: x[0] or ""
        ):
            parts.append(f"## {path}")
            for c in file_comments:
                loc = f"line {c.line}" if c.line else "general"
                parts.append(f"- [{c.reviewer}, {loc}]: {c.body}")
            parts.append("")

        parts.append(
            "Please address all comments, commit, and push."
        )
        return "\n".join(parts)

    def prioritize_comments(
        self, comments: list[ReviewComment]
    ) -> list[ReviewComment]:
        """Sort comments by priority.

        Order:
        1. CHANGES_REQUESTED reviews (blocking)
        2. Inline comments with file paths (specific)
        3. General review comments (broad)

        Args:
            comments: list of ReviewComment objects

        Returns:
            Sorted list of ReviewComment objects
        """
        def _sort_key(c: ReviewComment) -> tuple[int, str, int]:
            if c.state == "CHANGES_REQUESTED":
                priority = 0
            elif c.path:
                priority = 1
            else:
                priority = 2
            return (priority, c.path or "", c.line or 0)

        return sorted(comments, key=_sort_key)
