"""Pull request timeline: one row per PR with its phases, linked stories and flags.

Phases (each from the previous milestone to the next one that exists):

* ``coding``          first commit -> PR opened
* ``pickup``          PR opened -> first review by someone else
* ``review``          first review -> first approval (or merge, without an approval)
* ``merge``           approval -> merge
* ``deploy``          merge -> first successful production deployment that shipped it
                      (only when Azure Pipelines deployments are stored)

An open PR's current phase runs until now. See docs/pull-request-timeline.md.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.devops_config import DevOpsConfig
from app.devops_metrics import _is_success, _repo_keys
from app.models import AzurePipelineRun, Developer, PullRequest, Repository
from app.queries import active_in_window, in_branches
from app.teams import TeamConfig
from app.time_window import TimeWindow, utc_now_naive
from app.work_item_links import LinkIndex

PHASES = [
    ("coding", "Coding"),
    ("pickup", "Waiting for review"),
    ("review", "In review"),
    ("merge", "Waiting to merge"),
    ("deploy", "Waiting to deploy"),
]
STALE_OPEN_DAYS = 7


def _hours(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return max((later - earlier).total_seconds() / 3600, 0.0)


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _median(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return median(present) if present else None


def _status(pr: PullRequest) -> str:
    state = (pr.state or "").upper()
    if pr.merged_at is not None or state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "abandoned"
    if state == "OPEN":
        return "open"
    return "unknown"


class _Deployments:
    """Successful production deployments, to find the one that shipped a PR."""

    def __init__(self, db: Session, config: DevOpsConfig | None, since: datetime | None) -> None:
        self.by_sha: dict[str, AzurePipelineRun] = {}
        self.by_repo: dict[str, list[AzurePipelineRun]] = defaultdict(list)
        self.configured = False
        if config is None or since is None:
            return
        runs = (
            db.query(AzurePipelineRun)
            .filter(AzurePipelineRun.finish_time >= since)
            .order_by(AzurePipelineRun.finish_time)
            .all()
        )
        for run in runs:
            if not (config.is_deployment(run.definition_name) and config.is_production(run.definition_name)):
                continue
            self.configured = True
            if not _is_success(run):
                continue
            if run.source_version:
                self.by_sha.setdefault(run.source_version.lower(), run)
            for key in _repo_keys(run.repository_name):
                self.by_repo[key].append(run)

    def shipped(self, pr: PullRequest, repo_name: str) -> AzurePipelineRun | None:
        if pr.merged_at is None:
            return None
        run = self.by_sha.get((pr.merge_commit_sha or "").lower())
        if run is not None:
            return run
        for key in sorted(_repo_keys(repo_name), key=len, reverse=True):
            run = next((run for run in self.by_repo.get(key, []) if run.finish_time >= pr.merged_at), None)
            if run is not None:
                return run
        return None


def build_pr_rows(
    db: Session,
    window: TimeWindow,
    team_config: TeamConfig,
    links: LinkIndex,
    devops_config: DevOpsConfig | None = None,
    teams: list[str] | None = None,
    branches: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """PRs created or merged in ``window`` (optionally for ``teams`` / target ``branches``).

    Returns the rows and whether production deployments are stored (the deploy phase).
    """
    now = now or utc_now_naive()
    query = (
        db.query(PullRequest, Repository.name, Developer.github_username)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .options(selectinload(PullRequest.commits))
        .filter(active_in_window(window), in_branches(branches))
    )
    if teams is not None:
        query = query.filter(Developer.team_name.in_(teams))
    rows = query.all()
    merges = [pr.merged_at for pr, _, _ in rows if pr.merged_at]
    deployments = _Deployments(db, devops_config, min(merges) if merges else None)
    large = settings.large_pr_threshold_lines

    result = []
    for pr, repo_name, login in rows:
        status = _status(pr)
        commit_times = [commit.committed_at for commit in pr.commits if commit.committed_at]
        first_commit = min(commit_times, default=None)
        if first_commit and pr.created_at and first_commit > pr.created_at:
            first_commit = None  # commits pushed only after opening: no separate coding phase
        first_review = pr.first_reviewed_at or pr.first_review_comment_at
        approved = pr.approved_at
        deployment = deployments.shipped(pr, repo_name)
        deployed = deployment.finish_time if deployment else None
        end = pr.merged_at or (pr.closed_at if status == "abandoned" else None)

        # Milestones in order; each phase runs to the next milestone that exists.
        milestones = [
            ("coding", first_commit),
            ("pickup", pr.created_at),
            ("review", first_review if first_review and (end is None or first_review <= end) else None),
            ("merge", approved if approved and (end is None or approved <= end) else None),
            ("deploy", pr.merged_at if deployments.configured else None),
        ]
        present = [(phase, at) for phase, at in milestones if at is not None]
        timeline = []
        for index, (phase, start) in enumerate(present):
            if index + 1 < len(present):
                stop = present[index + 1][1]
            elif phase == "deploy":
                stop = deployed or now
            else:
                stop = end or now
            timeline.append(
                {
                    "phase": phase,
                    "label": dict(PHASES)[phase],
                    "start": _iso(start),
                    "end": _iso(stop if stop is not now else None),
                    "hours": _round(_hours(stop, start)),
                    "ongoing": stop is now,
                }
            )
        phase_hours = {item["phase"]: item["hours"] for item in timeline}

        work_items = links.for_pr(pr.id)
        stories = links.stories_for_pr(pr.id)
        unresolved = links.unresolved_by_pr.get(pr.id, [])
        member = team_config.member(login)
        lines = (pr.additions or 0) + (pr.deletions or 0)

        flags = []
        if not stories and not unresolved:
            flags.append("no_story")
        if unresolved:
            flags.append("story_not_synced")
        if status == "merged" and (pr.reviewers_count or 0) == 0:
            flags.append("unreviewed_merge")
        if lines > large:
            flags.append("large_pr")
        if (pr.requested_changes_count or 0) > 0:
            flags.append("changes_requested")
        if status == "open" and pr.created_at and now - pr.created_at > timedelta(days=STALE_OPEN_DAYS):
            flags.append("stale_open")
        if status == "abandoned":
            flags.append("abandoned")
        if status == "unknown":
            flags.append("unknown_state")
        if deployments.configured and status == "merged" and deployed is None and now - pr.merged_at > timedelta(hours=24):
            flags.append("not_deployed")

        result.append(
            {
                "id": pr.id,
                "repository": repo_name,
                "number": pr.pr_number,
                "title": pr.title,
                "url": f"https://github.com/{repo_name}/pull/{pr.pr_number}",
                "author": member.display_name if member else login,
                "author_login": login,
                "team": member.team if member else team_config.team_for(login),
                "status": status,
                "base_branch": pr.base_branch,
                "head_branch": pr.head_branch,
                "first_commit_at": _iso(first_commit),
                "created_at": _iso(pr.created_at),
                "first_review_at": _iso(first_review),
                "approved_at": _iso(approved),
                "merged_at": _iso(pr.merged_at),
                "closed_at": _iso(pr.closed_at),
                "deployed_at": _iso(deployed),
                "deployment": (
                    {"pipeline": deployment.definition_name, "build_number": deployment.build_number, "url": deployment.url}
                    if deployment
                    else None
                ),
                "coding_hours": phase_hours.get("coding"),
                "pickup_hours": phase_hours.get("pickup"),
                "review_hours": phase_hours.get("review"),
                "merge_hours": phase_hours.get("merge"),
                "deploy_hours": phase_hours.get("deploy"),
                "cycle_hours": _round(_hours(pr.merged_at, first_commit or pr.created_at)),
                "lead_to_production_hours": _round(_hours(deployed, first_commit or pr.created_at)),
                "age_hours": _round(_hours(now, pr.created_at)) if status == "open" else None,
                "additions": pr.additions or 0,
                "deletions": pr.deletions or 0,
                "changed_files": pr.changed_files or 0,
                "commits": pr.commit_count or 0,
                "reviewers": pr.reviewers_count or 0,
                "review_comments": pr.review_comments_count or 0,
                "changes_requested": pr.requested_changes_count or 0,
                "stories": stories,
                "work_items": [link.as_dict() for link in work_items],
                "unresolved_work_item_refs": unresolved,
                "flags": flags,
                "timeline": timeline,
            }
        )
    result.sort(key=lambda row: row["merged_at"] or row["created_at"] or "", reverse=True)
    return result, deployments.configured


def summarize_prs(rows: list[dict[str, Any]], deployments_configured: bool) -> dict[str, Any]:
    statuses = Counter(row["status"] for row in rows)
    merged = [row for row in rows if row["status"] == "merged"]
    flags = Counter(flag for row in rows for flag in row["flags"])
    total = len(rows)
    return {
        "pull_requests": total,
        "merged": statuses["merged"],
        "open": statuses["open"],
        "abandoned": statuses["abandoned"],
        "unknown_state": statuses["unknown"],
        "without_story": flags["no_story"],
        "without_story_pct": round(flags["no_story"] / total * 100, 1) if total else None,
        "story_not_synced": flags["story_not_synced"],
        "unreviewed_merges": flags["unreviewed_merge"],
        "median_hours": {phase: _round(_median(row[f"{phase}_hours"] for row in merged)) for phase, _ in PHASES},
        "median_cycle_hours": _round(_median(row["cycle_hours"] for row in merged)),
        "median_lead_to_production_hours": _round(_median(row["lead_to_production_hours"] for row in merged)),
        "deployments_configured": deployments_configured,
        "flags": dict(flags.most_common()),
    }


def pr_timeline_csv(rows: list[dict[str, Any]]) -> str:
    header = [
        "repository", "pr_number", "title", "status", "author", "team", "base_branch", "head_branch",
        "first_commit_at", "created_at", "first_review_at", "approved_at", "merged_at", "closed_at", "deployed_at",
        "coding_hours", "pickup_hours", "review_hours", "merge_hours", "deploy_hours", "cycle_hours",
        "lead_to_production_hours", "age_hours", "additions", "deletions", "changed_files", "commits", "reviewers",
        "review_comments", "changes_requested", "stories", "work_items", "unresolved_work_item_refs", "flags", "url",
    ]
    buffer = io.StringIO()
    buffer.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        values = {
            **row,
            "pr_number": row["number"],
            "stories": "; ".join(str(item) for item in row["stories"]),
            "work_items": "; ".join(f"{item['id']} ({item['type']})" for item in row["work_items"]),
            "unresolved_work_item_refs": "; ".join(str(item) for item in row["unresolved_work_item_refs"]),
            "flags": "; ".join(row["flags"]),
        }
        writer.writerow(["" if values.get(column) is None else values.get(column) for column in header])
    return buffer.getvalue()
