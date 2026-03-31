"""Specialized handler for CI failure parsing and error formatting."""

import logging
import re

logger = logging.getLogger(__name__)

# Common failure patterns across CI systems
PYTEST_FAIL_RE = re.compile(r"^(FAILED|ERROR)\s+.+", re.MULTILINE)
JEST_FAIL_RE = re.compile(r"^\s*(FAIL)\s+.+", re.MULTILINE)
GENERIC_ERROR_RE = re.compile(
    r"^.*\b(error|Error|ERROR|FAIL|FAILED|fatal)\b.*$", re.MULTILINE
)


class CIHandler:
    """Parses CI failure logs and formats them for agent consumption.

    Different CI systems produce different log formats.
    This handler normalizes them into a consistent format.
    """

    def parse_github_actions_log(self, log_text: str) -> str:
        """Parse a GitHub Actions log.

        Extracts the failing step and returns the relevant error output
        (last 100 lines of the failing step).

        Args:
            log_text: raw log text from GitHub Actions

        Returns:
            Relevant error output, trimmed to last 100 lines
        """
        lines = log_text.strip().splitlines()

        # Find the failing step marker
        fail_start = None
        for i, line in enumerate(lines):
            if "##[error]" in line or "Process completed with exit code" in line:
                # Walk back to find the step header
                fail_start = max(0, i - 50)
                break

        if fail_start is not None:
            relevant = lines[fail_start:]
        else:
            relevant = lines

        # Return last 100 lines
        tail = relevant[-100:] if len(relevant) > 100 else relevant
        return "\n".join(tail)

    def format_test_failures(self, raw_output: str) -> str:
        """Extract test failure details from pytest/jest/etc output.

        Looks for common patterns:
        - pytest: FAILED lines, assertion errors
        - jest: FAIL lines, expect().toBe() mismatches
        - generic: lines containing error/FAIL

        Args:
            raw_output: raw test runner output

        Returns:
            Formatted string with just the relevant failures
        """
        # Try pytest first
        pytest_matches = PYTEST_FAIL_RE.findall(raw_output)
        if pytest_matches:
            return self._format_pytest(raw_output, pytest_matches)

        # Try jest
        jest_matches = JEST_FAIL_RE.findall(raw_output)
        if jest_matches:
            return self._format_jest(raw_output)

        # Fallback to generic error lines
        error_lines = GENERIC_ERROR_RE.findall(raw_output)
        if error_lines:
            return "Errors found:\n" + "\n".join(
                f"  {line.strip()}" for line in error_lines[:30]
            )

        return "CI failed but no specific errors could be extracted."

    def _format_pytest(self, raw: str, matches: list[str]) -> str:
        """Format pytest failures."""
        parts = ["Test failures (pytest):\n"]
        for match in matches[:20]:
            parts.append(f"  {match.strip()}")

        # Also grab AssertionError details
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            if "AssertionError" in line or "assert " in line.lower():
                context = lines[max(0, i - 2):i + 3]
                parts.append("  ---")
                for ctx_line in context:
                    parts.append(f"  {ctx_line.strip()}")

        return "\n".join(parts)

    def _format_jest(self, raw: str) -> str:
        """Format jest failures."""
        parts = ["Test failures (jest):\n"]
        lines = raw.splitlines()
        in_failure = False

        for line in lines:
            if line.strip().startswith("FAIL"):
                in_failure = True
                parts.append(f"  {line.strip()}")
            elif in_failure:
                if line.strip().startswith(("PASS", "Test Suites:")):
                    in_failure = False
                else:
                    parts.append(f"    {line.rstrip()}")

        return "\n".join(parts[:50])
