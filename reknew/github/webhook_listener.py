"""Listen for GitHub webhook events or poll for PR status changes."""

import asyncio
import hashlib
import hmac
import logging
from typing import Any, Callable

from fastapi import FastAPI, Request, HTTPException

from reknew.config import ReknewConfig
from reknew.github.pr_manager import PRManager

logger = logging.getLogger(__name__)


class WebhookListener:
    """Listens for GitHub events via webhooks or polling.

    Mode is determined by config.github.mode:
    - "webhook": FastAPI endpoint receives POST events from GitHub
    - "poll": periodically check PR status via API

    Events emitted:
    - "ci-completed": CI finished (pass or fail) on a PR
    - "review-submitted": someone submitted a review on a PR
    - "pr-merged": a PR was merged
    """

    def __init__(self, config: ReknewConfig, pr_manager: PRManager):
        """Initialize.

        Args:
            config: validated ReknewConfig
            pr_manager: PRManager for checking CI/review status
        """
        self.config = config
        self.pr_manager = pr_manager
        self._callbacks: dict[str, list[Callable]] = {}
        self._watched_prs: dict[int, str] = {}  # pr_number -> task_id
        self._last_ci_status: dict[int, str] = {}
        self._last_review_count: dict[int, int] = {}

    def watch_pr(self, pr_number: int, task_id: str) -> None:
        """Start watching a PR for CI and review events.

        Args:
            pr_number: GitHub PR number
            task_id: the ReKnew task that owns this PR
        """
        self._watched_prs[pr_number] = task_id
        self._last_ci_status[pr_number] = "pending"
        self._last_review_count[pr_number] = 0
        logger.info("Watching PR #%d for task %s", pr_number, task_id)

    def unwatch_pr(self, pr_number: int) -> None:
        """Stop watching a PR.

        Args:
            pr_number: GitHub PR number
        """
        self._watched_prs.pop(pr_number, None)
        self._last_ci_status.pop(pr_number, None)
        self._last_review_count.pop(pr_number, None)
        logger.info("Unwatched PR #%d", pr_number)

    def on(self, event: str, callback: Callable) -> None:
        """Register callback for an event.

        Events: ci-completed, review-submitted, pr-merged

        Args:
            event: event name
            callback: callable(task_id, pr_number, data)
        """
        self._callbacks.setdefault(event, []).append(callback)

    def _fire(self, event: str, task_id: str, pr_number: int,
              data: Any = None) -> None:
        """Fire all callbacks for an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(task_id, pr_number, data)
            except Exception:
                logger.exception("Callback error for %s on PR #%d",
                                 event, pr_number)

    async def poll_loop(self, repo_name: str) -> None:
        """Polling mode: check all watched PRs periodically.

        Every config.github.poll_interval seconds:
        1. For each watched PR:
           a. Get CI status
           b. If status changed from last check -> emit "ci-completed"
           c. Get review comments
           d. If new comments since last check -> emit "review-submitted"

        Args:
            repo_name: "owner/repo" format
        """
        interval = self.config.github.poll_interval

        while self._watched_prs:
            for pr_number, task_id in list(self._watched_prs.items()):
                try:
                    await self._poll_single_pr(repo_name, pr_number, task_id)
                except Exception:
                    logger.exception("Error polling PR #%d", pr_number)
            await asyncio.sleep(interval)

    async def _poll_single_pr(self, repo_name: str, pr_number: int,
                               task_id: str) -> None:
        """Poll a single PR for status changes."""
        # Check CI
        ci = self.pr_manager.get_ci_status(repo_name, pr_number)
        last_ci = self._last_ci_status.get(pr_number, "pending")

        if ci.overall != last_ci and ci.overall != "pending":
            self._last_ci_status[pr_number] = ci.overall
            self._fire("ci-completed", task_id, pr_number, ci)
            logger.info("PR #%d CI status changed: %s -> %s",
                        pr_number, last_ci, ci.overall)

        # Check reviews
        comments = self.pr_manager.get_review_comments(repo_name, pr_number)
        last_count = self._last_review_count.get(pr_number, 0)

        if len(comments) > last_count:
            new_comments = comments[last_count:]
            self._last_review_count[pr_number] = len(comments)
            self._fire("review-submitted", task_id, pr_number, new_comments)
            logger.info("PR #%d has %d new review comments",
                        pr_number, len(new_comments))

        # Check if merged
        try:
            pr = self.pr_manager.api.get_pull(repo_name, pr_number)
            if pr.merged:
                self._fire("pr-merged", task_id, pr_number, None)
                self.unwatch_pr(pr_number)
        except Exception:
            pass

    def create_webhook_routes(self) -> FastAPI:
        """Webhook mode: return FastAPI app with POST /webhooks/github.

        Validates webhook signature using config.github.webhook_secret.
        Parses event type from X-GitHub-Event header.
        Emits appropriate events.

        Returns:
            FastAPI application with webhook route
        """
        app = FastAPI()
        secret = self.config.github.webhook_secret

        @app.post("/webhooks/github")
        async def handle_webhook(request: Request) -> dict:
            body = await request.body()

            if secret:
                sig_header = request.headers.get("X-Hub-Signature-256", "")
                if not self._verify_signature(body, secret, sig_header):
                    raise HTTPException(status_code=401,
                                        detail="Invalid signature")

            event_type = request.headers.get("X-GitHub-Event", "")
            payload = await request.json()

            self._process_webhook_event(event_type, payload)
            return {"status": "ok"}

        return app

    def _process_webhook_event(self, event_type: str,
                                payload: dict) -> None:
        """Process a webhook event payload.

        Args:
            event_type: GitHub event type header value
            payload: parsed JSON payload
        """
        if event_type == "check_run":
            action = payload.get("action", "")
            if action == "completed":
                pr_numbers = self._extract_pr_numbers(payload)
                for pr_num in pr_numbers:
                    task_id = self._watched_prs.get(pr_num)
                    if task_id:
                        self._fire("ci-completed", task_id, pr_num, payload)

        elif event_type == "pull_request_review":
            pr_num = payload.get("pull_request", {}).get("number")
            task_id = self._watched_prs.get(pr_num)
            if task_id:
                self._fire("review-submitted", task_id, pr_num, payload)

        elif event_type == "pull_request":
            action = payload.get("action", "")
            if action == "closed" and payload.get("pull_request", {}).get("merged"):
                pr_num = payload["pull_request"]["number"]
                task_id = self._watched_prs.get(pr_num)
                if task_id:
                    self._fire("pr-merged", task_id, pr_num, payload)
                    self.unwatch_pr(pr_num)

    @staticmethod
    def _extract_pr_numbers(payload: dict) -> list[int]:
        """Extract PR numbers from a check_run payload."""
        prs = payload.get("check_run", {}).get("pull_requests", [])
        return [pr["number"] for pr in prs if "number" in pr]

    @staticmethod
    def _verify_signature(body: bytes, secret: str,
                           signature_header: str) -> bool:
        """Verify GitHub webhook signature.

        Args:
            body: raw request body
            secret: webhook secret
            signature_header: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature_header.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)
