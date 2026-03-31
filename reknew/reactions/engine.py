"""Config-driven reactions engine for GitHub events."""

import logging

from reknew.agents.spawner import AgentSpawner
from reknew.config import ReknewConfig
from reknew.github.pr_manager import PRManager

logger = logging.getLogger(__name__)


class ReactionsEngine:
    """Config-driven reactions to GitHub events.

    Three event types with automated responses, all configurable via YAML.
    This component closes the automation loop:
    agent produces code -> CI runs -> if it fails -> agent fixes -> repeat.
    """

    def __init__(
        self,
        config: ReknewConfig,
        pr_manager: PRManager,
        spawner: AgentSpawner,
    ):
        """Initialize.

        Args:
            config: validated ReknewConfig
            pr_manager: PRManager for CI/review/merge operations
            spawner: AgentSpawner to re-prompt agents
        """
        self.config = config
        self.pr_manager = pr_manager
        self.spawner = spawner
        self._task_retry_counts: dict[str, int] = {}
        self._task_cost_tracking: dict[str, float] = {}

    async def handle_event(
        self,
        event_type: str,
        task_id: str,
        pr_number: int,
        repo_name: str,
    ) -> None:
        """Route an event to the appropriate handler.

        Args:
            event_type: "ci-completed", "review-submitted"
            task_id: the task that owns this PR
            pr_number: the GitHub PR number
            repo_name: "owner/repo"
        """
        if event_type == "ci-completed":
            ci = self.pr_manager.get_ci_status(repo_name, pr_number)
            if ci.overall == "failed":
                rule = self.config.reactions.get("ci-failed")
                if rule and rule.auto:
                    await self._handle_ci_failed(
                        task_id, pr_number, repo_name
                    )
                else:
                    logger.info(
                        "[Reactions] Task %s: CI failed but auto-handling "
                        "disabled, skipping",
                        task_id,
                    )
            elif ci.overall == "passed":
                rule = self.config.reactions.get("approved-and-green")
                if rule:
                    await self._handle_approved(
                        task_id, pr_number, repo_name
                    )

        elif event_type == "review-submitted":
            rule = self.config.reactions.get("changes-requested")
            if rule and rule.auto:
                await self._handle_changes_requested(
                    task_id, pr_number, repo_name
                )
            else:
                logger.info(
                    "[Reactions] Task %s: review submitted but "
                    "auto-handling disabled, skipping",
                    task_id,
                )

        else:
            logger.warning(
                "[Reactions] Unknown event type '%s' for task %s",
                event_type, task_id,
            )

    async def _handle_ci_failed(
        self, task_id: str, pr_number: int, repo_name: str
    ) -> None:
        """Handle CI failure on a PR.

        Steps:
        1. Check retry count against config max retries
        2. If exhausted -> close PR with comment, escalate to human
        3. Check cost limit
        4. Get CI error details
        5. Format error as feedback prompt
        6. Send feedback to agent
        7. Increment retry count
        """
        rule = self.config.reactions.get("ci-failed")
        max_retries = rule.retries if rule else 2
        retry_count = self._task_retry_counts.get(task_id, 0)

        if retry_count >= max_retries:
            logger.info(
                "[Reactions] Task %s: CI fix retries exhausted (%d/%d), "
                "escalating to human",
                task_id, retry_count, max_retries,
            )
            self.pr_manager.close_pr(
                repo_name,
                pr_number,
                f"ReKnew: CI fix retries exhausted ({retry_count}/{max_retries}). "
                "Escalating to human.",
            )
            return

        if not self._check_cost_limit(task_id):
            logger.info(
                "[Reactions] Task %s: cost limit exceeded, "
                "escalating to human",
                task_id,
            )
            self.pr_manager.close_pr(
                repo_name,
                pr_number,
                "ReKnew: cost limit exceeded. Escalating to human.",
            )
            return

        ci = self.pr_manager.get_ci_status(repo_name, pr_number)
        feedback = self.pr_manager.format_ci_errors(ci)

        info = self.spawner.running.get(task_id)
        if info:
            adapter = info["adapter"]
            adapter.send_feedback(info["handle"], feedback)

        self._task_retry_counts[task_id] = retry_count + 1
        logger.info(
            "[Reactions] Task %s: CI fix attempt %d/%d",
            task_id, retry_count + 1, max_retries,
        )

    async def _handle_changes_requested(
        self, task_id: str, pr_number: int, repo_name: str
    ) -> None:
        """Handle review comments requesting changes.

        Steps:
        1. Get review comments
        2. Format as feedback prompt
        3. Send to agent
        """
        comments = self.pr_manager.get_review_comments(repo_name, pr_number)
        if not comments:
            return

        feedback = self.pr_manager.format_review_comments(comments)

        info = self.spawner.running.get(task_id)
        if info:
            adapter = info["adapter"]
            adapter.send_feedback(info["handle"], feedback)

        logger.info(
            "[Reactions] Task %s: addressing %d review comment(s)",
            task_id, len(comments),
        )

    async def _handle_approved(
        self, task_id: str, pr_number: int, repo_name: str
    ) -> None:
        """Handle PR approved with green CI.

        If config says auto=true -> merge the PR.
        If config says auto=false -> log notification, wait for human.
        """
        rule = self.config.reactions.get("approved-and-green")
        if rule and rule.auto and rule.action == "auto-merge":
            merged = self.pr_manager.merge_pr(repo_name, pr_number)
            if merged:
                logger.info(
                    "[Reactions] Task %s: PR #%d auto-merged",
                    task_id, pr_number,
                )
            else:
                logger.error(
                    "[Reactions] Task %s: auto-merge failed for PR #%d",
                    task_id, pr_number,
                )
        else:
            logger.info(
                "[Reactions] Task %s: PR #%d approved and green, "
                "awaiting human merge",
                task_id, pr_number,
            )

    def _check_cost_limit(self, task_id: str) -> bool:
        """Check if task has exceeded max_cost for reactions.

        Returns:
            True if under limit, False if exceeded.
        """
        rule = self.config.reactions.get("ci-failed")
        max_cost = rule.max_cost if rule else 5.00
        current = self._task_cost_tracking.get(task_id, 0.0)
        return current < max_cost
