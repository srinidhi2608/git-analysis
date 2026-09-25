from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from app.models import Developer, PullRequest


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _hours_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None or later < earlier:
        return None
    return (later - earlier).total_seconds() / 3600


def _week_label(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _summary_template(window_days: int) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "pull_request_count": 0,
        "merged_pull_request_count": 0,
        "reviewed_pull_request_count": 0,
        "merge_frequency_per_week": 0.0,
        "average_lead_time_hours": None,
        "median_lead_time_hours": None,
        "average_time_to_first_review_hours": None,
        "review_coverage_rate": 0.0,
        "approval_rate": 0.0,
        "change_failure_proxy_rate": 0.0,
        "average_recovery_time_hours": None,
        "recovery_samples": 0,
    }


def _build_metrics(prs: list[PullRequest], window_days: int, trend_weeks: int) -> dict[str, Any]:
    summary = _summary_template(window_days)
    if not prs:
        return {"summary": summary, "weekly_trends": []}

    lead_times: list[float] = []
    first_review_times: list[float] = []
    recovery_times: list[float] = []

    reviewed_pr_count = 0
    approved_pr_count = 0
    requested_changes_merged_pr_count = 0
    merged_pr_count = 0

    trend_cutoff = _utc_now_naive() - timedelta(weeks=trend_weeks)
    trend_buckets: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "week": "",
            "merged_prs": 0,
            "lead_times": [],
            "first_review_times": [],
            "reviewed_prs": 0,
            "change_failure_proxy_count": 0,
        }
    )

    for pull_request in prs:
        summary["pull_request_count"] += 1

        lead_time = (
            (pull_request.cycle_time_minutes / 60)
            if pull_request.cycle_time_minutes is not None
            else _hours_between(pull_request.merged_at, pull_request.created_at)
        )
        first_review_time = _hours_between(
            pull_request.first_review_comment_at,
            pull_request.created_at,
        )

        is_reviewed = (pull_request.review_count or 0) > 0 or (pull_request.review_comments_count or 0) > 0
        if is_reviewed:
            reviewed_pr_count += 1
        if (pull_request.approvals_count or 0) > 0:
            approved_pr_count += 1

        if lead_time is not None:
            lead_times.append(lead_time)
        if first_review_time is not None:
            first_review_times.append(first_review_time)

        if (pull_request.requested_changes_count or 0) > 0:
            recovery_time = _hours_between(
                pull_request.merged_at,
                pull_request.last_review_comment_at,
            )
            if recovery_time is not None:
                recovery_times.append(recovery_time)

        if pull_request.merged_at is None:
            continue

        merged_pr_count += 1
        if (pull_request.requested_changes_count or 0) > 0:
            requested_changes_merged_pr_count += 1
        if pull_request.merged_at < trend_cutoff:
            continue

        week = _week_label(pull_request.merged_at)
        bucket = trend_buckets[week]
        bucket["week"] = week
        bucket["merged_prs"] += 1
        if lead_time is not None:
            bucket["lead_times"].append(lead_time)
        if first_review_time is not None:
            bucket["first_review_times"].append(first_review_time)
        if is_reviewed:
            bucket["reviewed_prs"] += 1
        if (pull_request.requested_changes_count or 0) > 0:
            bucket["change_failure_proxy_count"] += 1

    summary["merged_pull_request_count"] = merged_pr_count
    summary["reviewed_pull_request_count"] = reviewed_pr_count
    summary["merge_frequency_per_week"] = _round(merged_pr_count / max(window_days / 7, 1), 2)
    summary["average_lead_time_hours"] = _round(sum(lead_times) / len(lead_times) if lead_times else None)
    summary["median_lead_time_hours"] = _round(median(lead_times) if lead_times else None)
    summary["average_time_to_first_review_hours"] = _round(
        sum(first_review_times) / len(first_review_times) if first_review_times else None
    )
    summary["review_coverage_rate"] = _round(
        (reviewed_pr_count / summary["pull_request_count"]) * 100 if summary["pull_request_count"] else 0
    )
    summary["approval_rate"] = _round((approved_pr_count / reviewed_pr_count) * 100 if reviewed_pr_count else 0)
    summary["change_failure_proxy_rate"] = _round(
        (requested_changes_merged_pr_count / merged_pr_count) * 100 if merged_pr_count else 0
    )
    summary["average_recovery_time_hours"] = _round(
        sum(recovery_times) / len(recovery_times) if recovery_times else None
    )
    summary["recovery_samples"] = len(recovery_times)

    weekly_trends = []
    for week in sorted(trend_buckets):
        bucket = trend_buckets[week]
        reviewed_count = bucket["reviewed_prs"]
        weekly_trends.append(
            {
                "week": week,
                "merged_prs": bucket["merged_prs"],
                "average_lead_time_hours": _round(
                    sum(bucket["lead_times"]) / len(bucket["lead_times"]) if bucket["lead_times"] else None
                ),
                "average_time_to_first_review_hours": _round(
                    sum(bucket["first_review_times"]) / len(bucket["first_review_times"])
                    if bucket["first_review_times"]
                    else None
                ),
                "change_failure_proxy_rate": _round(
                    (bucket["change_failure_proxy_count"] / bucket["merged_prs"]) * 100 if bucket["merged_prs"] else 0
                ),
            }
        )

    return {"summary": summary, "weekly_trends": weekly_trends}


def get_team_dora_metrics(db: Session, window_days: int = 30, trend_weeks: int = 8) -> dict[str, Any]:
    since = _utc_now_naive() - timedelta(days=window_days)
    prs = (
        db.query(PullRequest)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .filter(PullRequest.created_at >= since)
        .order_by(PullRequest.created_at.desc())
        .all()
    )
    return _build_metrics(prs, window_days=window_days, trend_weeks=trend_weeks)


def get_developer_dora_metrics(
    db: Session,
    github_username: str,
    window_days: int = 30,
    trend_weeks: int = 8,
) -> dict[str, Any] | None:
    developer = db.query(Developer).filter(Developer.github_username == github_username).one_or_none()
    if developer is None:
        return None

    since = _utc_now_naive() - timedelta(days=window_days)
    prs = (
        db.query(PullRequest)
        .filter(
            PullRequest.developer_id == developer.id,
            PullRequest.created_at >= since,
        )
        .order_by(PullRequest.created_at.desc())
        .all()
    )
    metrics = _build_metrics(prs, window_days=window_days, trend_weeks=trend_weeks)
    return {
        "github_username": developer.github_username,
        "team_name": developer.team_name,
        **metrics,
    }
