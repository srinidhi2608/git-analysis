"""Extensible metrics computation engine for pull-request analytics."""

from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO-8601 string or return a datetime as-is; returns None if falsy."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseMetricCalculator(abc.ABC):
    """Abstract base class for all PR metric calculators.

    Subclasses must implement :meth:`calculate`.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable metric name, used as the key in results."""

    @abc.abstractmethod
    def calculate(self, pr_data: dict[str, Any]) -> Any:
        """Compute the metric for a single PR.

        Parameters
        ----------
        pr_data:
            Dictionary representing one pull request.  The expected keys
            depend on the concrete calculator; unknown keys are ignored.

        Returns
        -------
        Any
            The computed metric value, or ``None`` when it cannot be
            determined from the available data.
        """


# ---------------------------------------------------------------------------
# Concrete calculators
# ---------------------------------------------------------------------------


class CycleTimeCalculator(BaseMetricCalculator):
    """Calculates PR cycle time in hours (created_at → merged_at).

    Returns ``None`` if the PR has not been merged or if either timestamp
    is missing.

    Expected PR data keys
    ---------------------
    created_at : str | datetime
    merged_at  : str | datetime | None
    """

    @property
    def name(self) -> str:
        return "cycle_time_hours"

    def calculate(self, pr_data: dict[str, Any]) -> float | None:
        created = _parse_dt(pr_data.get("created_at"))
        merged = _parse_dt(pr_data.get("merged_at"))
        if created is None or merged is None:
            return None
        if merged < created:
            logger.warning(
                "PR #%s: merged_at (%s) is before created_at (%s); skipping.",
                pr_data.get("pr_number", "?"),
                merged,
                created,
            )
            return None
        delta = merged - created
        return round(delta.total_seconds() / 3600, 4)


class ReworkCalculator(BaseMetricCalculator):
    """Estimates rework as the number of commits pushed after the first review comment.

    A commit is considered "rework" if its ``committed_at`` (or
    ``authored_at``) timestamp is later than the timestamp of the first
    review comment on the PR.

    Returns ``None`` when the required fields are absent.

    Expected PR data keys
    ---------------------
    commits : list[dict]
        Each commit dict should contain at least one of:
        ``committed_at``, ``authored_at``, or ``timestamp`` (str | datetime).
    first_review_comment_at : str | datetime | None
        Timestamp of the earliest review comment.  If ``None`` or missing,
        rework count is 0 (no review comments ⇒ no post-review commits).
    """

    @property
    def name(self) -> str:
        return "rework_commit_count"

    def calculate(self, pr_data: dict[str, Any]) -> int | None:
        first_review_at = _parse_dt(pr_data.get("first_review_comment_at"))
        if first_review_at is None:
            # No review comment recorded → no post-review commits.
            return 0

        commits = pr_data.get("commits")
        if not isinstance(commits, list):
            return None
        commits = commits or []

        rework_count = 0
        for commit in commits:
            committed_at = _parse_dt(
                commit.get("committed_at")
                or commit.get("authored_at")
                or commit.get("timestamp")
            )
            if committed_at is not None and committed_at > first_review_at:
                rework_count += 1

        return rework_count


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class MetricsRunner:
    """Runs a list of metric calculators over a list of PR data dicts.

    Parameters
    ----------
    calculators:
        Ordered list of :class:`BaseMetricCalculator` instances to apply.

    Example
    -------
    >>> runner = MetricsRunner([CycleTimeCalculator(), ReworkCalculator()])
    >>> results = runner.run(pr_data_list)
    """

    def __init__(self, calculators: list[BaseMetricCalculator]) -> None:
        if not calculators:
            raise ValueError("At least one calculator must be provided.")
        self.calculators = calculators

    def run(self, pr_data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compute all metrics for every PR.

        Parameters
        ----------
        pr_data_list:
            List of PR data dicts.

        Returns
        -------
        list[dict]
            One result dict per PR, containing the original PR identifiers
            (``repository``, ``pr_number``) plus one key per calculator.
        """
        results: list[dict[str, Any]] = []
        for pr in pr_data_list:
            row: dict[str, Any] = {
                "repository": pr.get("repository"),
                "pr_number": pr.get("pr_number"),
            }
            for calc in self.calculators:
                try:
                    row[calc.name] = calc.calculate(pr)
                except Exception:
                    logger.exception(
                        "Calculator %r failed on PR #%s.",
                        calc.name,
                        pr.get("pr_number", "?"),
                    )
                    row[calc.name] = None
            results.append(row)
        return results
