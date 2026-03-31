"""Route tasks to human teams or AI agents based on configurable rules."""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CONDITION_RE = re.compile(
    r"^(?:(?P<field>\w[\w.]*):"
    r"(?P<op>>?)(?P<value>[^\s]+)"
    r"|(?P<default>default))"
    r"\s*->\s*(?P<target>human|ai)$"
)


@dataclass
class RoutingDecision:
    """Result of routing a single task."""

    task_id: str
    target: str  # "human" or "ai"
    matched_rule: str
    confidence: str  # "exact" or "default"


class CapacityRouter:
    """Routes tasks to human or AI based on YAML rules.

    Rules are evaluated in order. First match wins.
    Supported conditions:
    - label:{value}
    - complexity:{low|medium|high}
    - priority:{p0|p1|p2|p3}
    - file_count:>{N}
    - estimated_minutes:>{N}
    - default
    """

    def __init__(self, rules: list[str]):
        """Parse rule strings into structured rules.

        Args:
            rules: list from config, e.g. ["label:critical -> human"]

        Raises:
            ValueError: if any rule has invalid format or rules list is empty
        """
        if not rules:
            raise ValueError("At least one rule required")
        self.rules = self._parse_rules(rules)

    def _parse_rules(self, raw_rules: list[str]) -> list[dict]:
        """Parse 'condition -> target' strings into structured dicts."""
        parsed: list[dict] = []
        has_default = False

        for raw in raw_rules:
            m = CONDITION_RE.match(raw.strip())
            if not m:
                raise ValueError(
                    f"Invalid rule format: '{raw}'. "
                    "Expected 'field:value -> human|ai' or "
                    "'default -> human|ai'"
                )

            target = m.group("target")
            if target not in ("human", "ai"):
                raise ValueError(
                    f"Invalid target '{target}' in rule '{raw}': "
                    "must be 'human' or 'ai'"
                )

            if m.group("default"):
                parsed.append({
                    "type": "default",
                    "field": None,
                    "operator": None,
                    "value": None,
                    "target": target,
                    "raw": raw.strip(),
                })
                has_default = True
            else:
                field = m.group("field")
                op = "gt" if m.group("op") == ">" else "eq"
                value: str | int = m.group("value")
                if op == "gt":
                    value = int(value)
                parsed.append({
                    "type": "match",
                    "field": field,
                    "operator": op,
                    "value": value,
                    "target": target,
                    "raw": raw.strip(),
                })

        if not has_default:
            logger.warning(
                "No default rule found. Tasks that match no rule "
                "will not be routed."
            )

        return parsed

    def route(
        self, task: dict, issue_labels: list[str] | None = None
    ) -> RoutingDecision:
        """Determine if a task goes to human or AI.

        Args:
            task: task dict from task_breakdown
            issue_labels: labels from the original GitHub issue

        Returns:
            RoutingDecision with target, matched rule, and confidence
        """
        labels = issue_labels or []

        for rule in self.rules:
            if rule["type"] == "default":
                return RoutingDecision(
                    task_id=task["id"],
                    target=rule["target"],
                    matched_rule=rule["raw"],
                    confidence="default",
                )

            field = rule["field"]
            op = rule["operator"]
            value = rule["value"]

            if field == "label":
                if op == "eq" and value in labels:
                    return RoutingDecision(
                        task_id=task["id"],
                        target=rule["target"],
                        matched_rule=rule["raw"],
                        confidence="exact",
                    )

            elif field == "complexity":
                if op == "eq" and task.get("complexity") == value:
                    return RoutingDecision(
                        task_id=task["id"],
                        target=rule["target"],
                        matched_rule=rule["raw"],
                        confidence="exact",
                    )

            elif field == "priority":
                if op == "eq" and value in labels:
                    return RoutingDecision(
                        task_id=task["id"],
                        target=rule["target"],
                        matched_rule=rule["raw"],
                        confidence="exact",
                    )

            elif field == "file_count":
                file_count = len(task.get("files", []))
                if op == "gt" and file_count > value:
                    return RoutingDecision(
                        task_id=task["id"],
                        target=rule["target"],
                        matched_rule=rule["raw"],
                        confidence="exact",
                    )

            elif field == "estimated_minutes":
                est = task.get("estimated_minutes", 0)
                if op == "gt" and est > value:
                    return RoutingDecision(
                        task_id=task["id"],
                        target=rule["target"],
                        matched_rule=rule["raw"],
                        confidence="exact",
                    )

        # No rule matched at all
        return RoutingDecision(
            task_id=task["id"],
            target="ai",
            matched_rule="(no match)",
            confidence="default",
        )

    def route_batch(
        self, tasks: list[dict], issue_labels: list[str] | None = None
    ) -> dict[str, RoutingDecision]:
        """Route all tasks from a breakdown.

        Args:
            tasks: list of task dicts
            issue_labels: labels from the GitHub issue

        Returns:
            {task_id: RoutingDecision}
        """
        return {
            t["id"]: self.route(t, issue_labels) for t in tasks
        }

    def get_capacity_summary(
        self, decisions: dict[str, RoutingDecision]
    ) -> dict:
        """Summarize routing decisions.

        Args:
            decisions: dict from route_batch

        Returns:
            Summary dict with counts and task ID lists
        """
        human_tasks = [
            tid for tid, d in decisions.items() if d.target == "human"
        ]
        ai_tasks = [
            tid for tid, d in decisions.items() if d.target == "ai"
        ]
        rules_matched: dict[str, int] = {}
        for d in decisions.values():
            rules_matched[d.matched_rule] = (
                rules_matched.get(d.matched_rule, 0) + 1
            )

        return {
            "total": len(decisions),
            "human": len(human_tasks),
            "ai": len(ai_tasks),
            "human_tasks": human_tasks,
            "ai_tasks": ai_tasks,
            "rules_matched": rules_matched,
        }
