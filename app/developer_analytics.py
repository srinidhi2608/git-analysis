from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any, Protocol

import requests
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Developer, PullRequest

logger = logging.getLogger(__name__)

COMMENT_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "style": ("style", "naming", "readability", "format", "lint", "convention", "clean up"),
    "correctness": ("bug", "logic", "edge case", "null", "undefined", "incorrect", "broken", "fix"),
    "testing": ("test", "coverage", "assert", "spec", "unit test", "integration test"),
    "maintainability": ("refactor", "duplicate", "simplify", "maintain", "structure", "modular"),
    "performance": ("performance", "slow", "efficient", "optimize", "latency", "n+1"),
    "security": ("security", "sanitize", "validate", "auth", "permission", "secret", "xss", "csrf"),
}


@dataclass
class DeveloperAnalyticsSnapshot:
    github_username: str
    metrics: dict[str, Any]
    breakdown: dict[str, Any]
    sample: dict[str, int]


class DeveloperAnalyticsNarrator(Protocol):
    provider_name: str

    def summarize(self, snapshot: DeveloperAnalyticsSnapshot) -> dict[str, Any]:
        ...


def _clamp(score: float, lower: int = 0, upper: int = 100) -> int:
    return max(lower, min(upper, round(score)))


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _assessment(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Mixed"
    if score >= 40:
        return "Needs attention"
    return "High concern"


def _risk_label(score: int) -> str:
    if score >= 70:
        return "High"
    if score >= 40:
        return "Moderate"
    return "Low"


class HeuristicDeveloperAnalyticsNarrator:
    provider_name = "heuristic-fallback"

    def summarize(self, snapshot: DeveloperAnalyticsSnapshot) -> dict[str, Any]:
        metrics = snapshot.metrics
        breakdown = snapshot.breakdown
        sample = snapshot.sample

        prs = sample["pull_requests"]
        comment_text_items = sample["comment_text_items"]
        comments_per_pr = metrics["average_comments_per_pr"]
        requested_changes_rate = metrics["requested_changes_rate"]
        rework = metrics["average_rework_commits_per_pr"]
        avg_pr_size = metrics["average_pr_size"]
        avg_followup = metrics["average_time_to_first_followup_hours"]
        avg_resolution = metrics["average_time_to_merge_after_feedback_hours"]
        risk_score = _clamp(
            (requested_changes_rate * 0.5)
            + min(rework * 18, 30)
            + min(comments_per_pr * 8, 25)
            + min(metrics["large_pr_rate"] * 0.4, 20)
        )

        categorized_total = sum(item["count"] for item in breakdown["comment_categories"])
        correctness_security = sum(
            item["count"]
            for item in breakdown["comment_categories"]
            if item["category"] in {"correctness", "security"}
        )
        coding_score = _clamp(
            92
            - min(comments_per_pr * 7, 25)
            - min(requested_changes_rate * 0.45, 20)
            - (0 if categorized_total == 0 else (correctness_security / categorized_total) * 18)
        )
        efficiency_score = _clamp(
            90
            - min(max(avg_pr_size - 450, 0) / 18, 22)
            - min(comments_per_pr * 6, 18)
            - min(rework * 14, 24)
            - min((metrics["average_cycle_time_hours"] or 0) / 6, 18)
        )
        responsiveness_score = (
            None
            if avg_followup is None and avg_resolution is None
            else _clamp(
                94
                - min((avg_followup or 0) * 2.5, 36)
                - min((avg_resolution or 0) / 4, 28)
            )
        )

        top_categories = [item for item in breakdown["comment_categories"] if item["count"] > 0][:3]
        repeated_categories = [item["category"] for item in breakdown["repeated_issue_categories"][:3]]

        confidence = "low"
        if prs >= 5 and comment_text_items >= 8:
            confidence = "medium"
        if prs >= 8 and comment_text_items >= 15 and avg_followup is not None:
            confidence = "high"

        overview_lead = (
            f"Based on {prs} saved {_pluralize(prs, 'PR', 'PRs')} "
            f"and {comment_text_items} review {_pluralize(comment_text_items, 'comment')}"
            if comment_text_items
            else f"Based on {prs} saved {_pluralize(prs, 'PR', 'PRs')} with limited saved review text"
        )
        overview_parts = [overview_lead]
        if comment_text_items:
            if coding_score >= 70:
                overview_parts.append("Coding standards look generally solid")
            elif coding_score >= 55:
                overview_parts.append("Coding standards look mixed")
            else:
                overview_parts.append("Review feedback suggests recurring coding standard issues")

        if responsiveness_score is None:
            overview_parts.append("Response-speed estimates are limited by sparse timestamped review data")
        elif responsiveness_score >= 70:
            overview_parts.append("Follow-up after review feedback appears reasonably fast")
        else:
            overview_parts.append("Review follow-up appears slower than ideal")

        highlights: list[str] = []
        if requested_changes_rate <= 20:
            highlights.append(f"Only {requested_changes_rate:.1f}% of PRs show requested changes.")
        if avg_followup is not None and avg_followup <= 12:
            highlights.append(f"Average first follow-up after review feedback is about {avg_followup:.1f} hours.")
        if top_categories:
            highlights.append(
                "Most review feedback is concentrated in "
                + ", ".join(item["category"] for item in top_categories[:2])
                + "."
            )
        if not highlights:
            highlights.append("Saved review history is available, but there are not enough strong positive signals for a specific highlight.")

        risks: list[str] = []
        if repeated_categories:
            risks.append("Repeated review themes: " + ", ".join(repeated_categories) + ".")
        if avg_pr_size > 600:
            risks.append(f"Average PR size is {avg_pr_size:.0f} changed lines, which can slow reviews.")
        if avg_resolution is not None and avg_resolution > 24:
            risks.append(
                f"Average time from last review feedback to merge is {avg_resolution:.1f} hours."
            )
        if comment_text_items == 0:
            risks.append("The database has PR-level counts, but little or no saved review text to ground detailed analysis.")
        if not risks:
            risks.append("No major struggle signal stands out in the saved review history.")

        recommendations: list[str] = []
        if any(item["category"] == "testing" for item in top_categories):
            recommendations.append("Strengthen test coverage before review when changes touch logic-heavy code.")
        if any(item["category"] in {"style", "maintainability"} for item in top_categories):
            recommendations.append("Use local linting and small cleanup passes before opening PRs to reduce style churn.")
        if avg_pr_size > 600:
            recommendations.append("Split large changes into smaller PRs to improve review throughput.")
        if avg_followup is not None and avg_followup > 12:
            recommendations.append("Aim for faster first follow-up on review feedback to reduce cycle time.")
        if not recommendations:
            recommendations.append("Keep PRs small and continue resolving review feedback with the current pace.")

        categories = [
            {
                "key": "coding_standards",
                "label": "Coding standards",
                "score": coding_score,
                "assessment": _assessment(coding_score),
                "evidence": [
                    f"{metrics['total_review_comments']} total saved PR/review comments across {prs} PRs.",
                    (
                        "Top feedback themes: "
                        + ", ".join(item["category"] for item in top_categories)
                        if top_categories
                        else "Not enough saved comment text to identify recurring feedback themes."
                    ),
                ],
            },
            {
                "key": "pr_efficiency",
                "label": "PR efficiency",
                "score": efficiency_score,
                "assessment": _assessment(efficiency_score),
                "evidence": [
                    f"Average PR size is {avg_pr_size:.1f} changed lines across {metrics['average_changed_files']:.1f} files.",
                    f"Average review comment density is {metrics['comment_density_per_100_lines']:.2f} comments per 100 changed lines.",
                ],
            },
            {
                "key": "responsiveness",
                "label": "Review responsiveness",
                "score": responsiveness_score,
                "assessment": _assessment(responsiveness_score) if responsiveness_score is not None else "Insufficient data",
                "evidence": [
                    (
                        f"Average first follow-up after feedback is {avg_followup:.1f} hours."
                        if avg_followup is not None
                        else "No timestamped follow-up commits were available for a reliable response-speed estimate."
                    ),
                    (
                        f"Average time from last feedback to merge is {avg_resolution:.1f} hours."
                        if avg_resolution is not None
                        else "Merge-after-feedback timing could not be derived from the saved PR history."
                    ),
                ],
            },
            {
                "key": "struggle_risk",
                "label": "Struggle risk",
                "score": risk_score,
                "assessment": _risk_label(risk_score),
                "evidence": [
                    f"{requested_changes_rate:.1f}% of PRs include at least one requested-changes review.",
                    f"Average rework after feedback is {rework:.2f} commits per PR.",
                ],
            },
        ]

        return {
            "provider": self.provider_name,
            "confidence": confidence,
            "overview": ". ".join(part.rstrip(".") for part in overview_parts) + ".",
            "categories": categories,
            "highlights": highlights,
            "risks": risks,
            "recommendations": recommendations,
        }


class HttpDeveloperAnalyticsNarrator:
    provider_name = "configured-http-provider"

    def summarize(self, snapshot: DeveloperAnalyticsSnapshot) -> dict[str, Any]:
        if not settings.developer_analytics_ai_url or not settings.developer_analytics_ai_token:
            raise RuntimeError("AI provider is not configured")

        prompt = {
            "developer": snapshot.github_username,
            "metrics": snapshot.metrics,
            "breakdown": snapshot.breakdown,
            "sample": snapshot.sample,
            "instructions": {
                "format": {
                    "provider": "string",
                    "confidence": "low|medium|high",
                    "overview": "string",
                    "categories": [
                        {
                            "key": "coding_standards|pr_efficiency|responsiveness|struggle_risk",
                            "label": "string",
                            "score": "0-100 integer or null",
                            "assessment": "string",
                            "evidence": ["string"],
                        }
                    ],
                    "highlights": ["string"],
                    "risks": ["string"],
                    "recommendations": ["string"],
                },
                "constraints": [
                    "Ground every claim in provided metrics or saved review text.",
                    "If data is sparse, explicitly say so.",
                    "Return valid JSON only.",
                ],
            },
        }

        response = requests.post(
            settings.developer_analytics_ai_url,
            headers={
                "Authorization": "Bearer " + settings.developer_analytics_ai_token,
                "Content-Type": "application/json",
            },
            json={
                "model": settings.developer_analytics_ai_model or "developer-analytics",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an engineering analytics assistant that summarizes PR review behavior using only the supplied evidence.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        parsed = json.loads(content)
        parsed["provider"] = self.provider_name
        return parsed


def _build_narrator() -> DeveloperAnalyticsNarrator:
    if settings.developer_analytics_ai_url and settings.developer_analytics_ai_token:
        return HttpDeveloperAnalyticsNarrator()
    return HeuristicDeveloperAnalyticsNarrator()


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _categorize_comment(body: str) -> set[str]:
    lowered = body.lower()
    matched = {
        category
        for category, keywords in COMMENT_CATEGORY_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    }
    return matched or {"uncategorized"}


def _hours_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None or later < earlier:
        return None
    return (later - earlier).total_seconds() / 3600


def get_developer_review_analytics(db: Session, github_username: str) -> dict[str, Any] | None:
    developer = (
        db.query(Developer)
        .options(
            selectinload(Developer.pull_requests).selectinload(PullRequest.comments),
            selectinload(Developer.pull_requests).selectinload(PullRequest.commits),
        )
        .filter(Developer.github_username == github_username)
        .one_or_none()
    )
    if developer is None:
        return None

    prs = sorted(
        developer.pull_requests,
        key=lambda pull_request: pull_request.created_at or datetime.min,
        reverse=True,
    )
    total_changed_lines = 0
    total_changed_files = 0
    total_review_comments = 0
    requested_changes_prs = 0
    large_prs = 0
    rework_counts: list[int] = []
    cycle_times: list[float] = []
    followup_hours: list[float] = []
    resolution_hours: list[float] = []
    comment_categories: Counter[str] = Counter()
    category_prs: defaultdict[str, set[int]] = defaultdict(set)
    pr_breakdown: list[dict[str, Any]] = []
    saved_comment_text_items = 0

    for pull_request in prs:
        pr_size = (pull_request.additions or 0) + (pull_request.deletions or 0)
        total_changed_lines += pr_size
        total_changed_files += pull_request.changed_files or 0
        total_review_comments += pull_request.review_comments_count or 0
        if (pull_request.requested_changes_count or 0) > 0:
            requested_changes_prs += 1
        if pr_size >= 600:
            large_prs += 1
        if pull_request.cycle_time_minutes is not None:
            cycle_times.append(pull_request.cycle_time_minutes / 60)

        feedback_items = sorted(
            [
                comment
                for comment in pull_request.comments
                if comment.author_login and comment.author_login != github_username
            ],
            key=lambda comment: comment.created_at or datetime.min,
        )
        feedback_timestamps = [comment.created_at for comment in feedback_items if comment.created_at is not None]
        first_feedback_at = feedback_timestamps[0] if feedback_timestamps else pull_request.first_review_comment_at
        last_feedback_at = feedback_timestamps[-1] if feedback_timestamps else pull_request.last_review_comment_at

        for comment in feedback_items:
            body = (comment.body or "").strip()
            if not body:
                continue
            saved_comment_text_items += 1
            for category in _categorize_comment(body):
                comment_categories[category] += 1
                category_prs[category].add(pull_request.pr_number)

        commit_count = pull_request.commit_count or len(pull_request.commits)
        rework_after_feedback = 0
        first_followup_at: datetime | None = None
        if first_feedback_at is not None:
            for commit in sorted(
                [commit for commit in pull_request.commits if commit.committed_at is not None],
                key=lambda commit: commit.committed_at or datetime.min,
            ):
                if commit.committed_at and commit.committed_at > first_feedback_at:
                    rework_after_feedback += 1
                    if first_followup_at is None:
                        first_followup_at = commit.committed_at
        else:
            rework_after_feedback = max(commit_count - 1, 0)
        rework_counts.append(rework_after_feedback)

        followup_time = _hours_between(first_followup_at, first_feedback_at)
        if followup_time is not None:
            followup_hours.append(followup_time)

        resolution_time = _hours_between(pull_request.merged_at, last_feedback_at)
        if resolution_time is not None:
            resolution_hours.append(resolution_time)

        pr_breakdown.append(
            {
                "pr_number": pull_request.pr_number,
                "title": pull_request.title,
                "created_at": pull_request.created_at.isoformat() if pull_request.created_at else None,
                "merged_at": pull_request.merged_at.isoformat() if pull_request.merged_at else None,
                "review_comments": pull_request.review_comments_count or 0,
                "requested_changes": pull_request.requested_changes_count or 0,
                "rework_commits": rework_after_feedback,
                "size": pr_size,
            }
        )

    pr_count = len(prs)
    merged_pr_count = len([pull_request for pull_request in prs if pull_request.merged_at is not None])
    sample = {
        "pull_requests": pr_count,
        "comment_text_items": saved_comment_text_items,
        "followup_samples": len(followup_hours),
        "resolution_samples": len(resolution_hours),
    }
    metrics = {
        "pull_request_count": pr_count,
        "merged_pull_request_count": merged_pr_count,
        "total_review_comments": total_review_comments,
        "average_comments_per_pr": _round(total_review_comments / pr_count if pr_count else 0),
        "average_pr_size": _round(total_changed_lines / pr_count if pr_count else 0),
        "average_changed_files": _round(total_changed_files / pr_count if pr_count else 0),
        "largest_pr_size": max((item["size"] for item in pr_breakdown), default=0),
        "comment_density_per_100_lines": _round((total_review_comments / total_changed_lines) * 100 if total_changed_lines else 0),
        "requested_changes_rate": _round((requested_changes_prs / pr_count) * 100 if pr_count else 0),
        "average_rework_commits_per_pr": _round(mean(rework_counts) if rework_counts else 0),
        "average_cycle_time_hours": _round(mean(cycle_times) if cycle_times else None),
        "average_time_to_first_followup_hours": _round(mean(followup_hours) if followup_hours else None),
        "average_time_to_merge_after_feedback_hours": _round(mean(resolution_hours) if resolution_hours else None),
        "large_pr_rate": _round((large_prs / pr_count) * 100 if pr_count else 0),
    }
    breakdown = {
        "comment_categories": [
            {"category": category, "count": count}
            for category, count in comment_categories.most_common()
        ],
        "repeated_issue_categories": [
            {"category": category, "count": comment_categories[category], "pull_request_count": len(pr_numbers)}
            for category, pr_numbers in sorted(
                category_prs.items(),
                key=lambda item: (len(item[1]), comment_categories[item[0]]),
                reverse=True,
            )
            if len(pr_numbers) >= 2
        ],
        "pull_requests": pr_breakdown[:10],
    }

    snapshot = DeveloperAnalyticsSnapshot(
        github_username=github_username,
        metrics=metrics,
        breakdown=breakdown,
        sample=sample,
    )
    narrator = _build_narrator()
    summary: dict[str, Any]
    try:
        summary = narrator.summarize(snapshot)
    except Exception:
        logger.exception("Developer analytics narrator failed for %s; falling back.", github_username)
        summary = HeuristicDeveloperAnalyticsNarrator().summarize(snapshot)

    return {
        "github_username": developer.github_username,
        "team_name": developer.team_name,
        "metrics": metrics,
        "breakdown": breakdown,
        "sample": sample,
        "summary": summary,
    }
