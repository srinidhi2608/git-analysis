"""DevOps Integration metrics: Azure Boards stories, people efficiency and DORA.

DORA follows Apache DevLake's definitions and its DORA 2023 benchmarks:

* Deployment: a completed run of a pipeline whose name matches
  ``deployment_pattern`` (and ``production_pattern`` if set). Results
  ``succeeded``/``partiallySucceeded`` are successes; ``failed``/``canceled``
  are failures (DevLake's Azure DevOps mapping).
* Deployment frequency: tier from the median number of days with a successful
  production deployment per week / per month in the date range.
* Lead time for changes: PR first commit -> finish of the successful production
  deployment that shipped it (the deployment of the PR's merge commit; else the
  first deployment of that repository after the merge). Median.
* Change failure rate: with incidents configured, the share of deployments
  followed by an incident (each incident is linked to the last successful
  production deployment before it, like DevLake); otherwise the share of
  failed deployment runs.
* Failed deployment recovery time: with incidents, deployment finish -> incident
  closed; otherwise failed run -> next successful run of the same pipeline. Median.

For a person or team the same metrics are computed over the deployments that
shipped their pull requests.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import mean, median
from typing import Any, Iterable

from sqlalchemy.orm import Session, selectinload

from app.devops_config import DevOpsConfig
from app.models import (
    AzurePipelineRun,
    AzureWorkItem,
    AzureWorkItemTag,
    Commit,
    Developer,
    PullRequest,
    Repository,
)
from app.teams import TeamConfig

SUCCESS_RESULTS = {"succeeded", "partiallysucceeded"}
FAILURE_RESULTS = {"failed", "canceled", "none"}
AB_LINK = re.compile(r"AB#(\d+)", re.IGNORECASE)
# How far before the date range to look for PRs/deployments that ship in it.
LOOKBACK_FOR_LINKING = timedelta(days=90)

BENCHMARKS = {
    "deployment_frequency": {
        "Elite": "On-demand (5+ deployment days per week)",
        "High": "Between once per day and once per week",
        "Medium": "Between once per week and once per month",
        "Low": "Fewer than once per month",
    },
    "lead_time": {
        "Elite": "Less than one day",
        "High": "Between one day and one week",
        "Medium": "Between one week and one month",
        "Low": "More than one month",
    },
    "change_failure_rate": {"Elite": "0-5%", "High": "5-10%", "Medium": "10-15%", "Low": "More than 15%"},
    "recovery_time": {
        "Elite": "Less than one hour",
        "High": "Less than one day",
        "Medium": "Between one day and one week",
        "Low": "More than one week",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hours(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None or later < earlier:
        return None
    return (later - earlier).total_seconds() / 3600


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _range_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _iso_week(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _weeks_in_range(date_from: date, date_to: date) -> list[date]:
    weeks, current = [], _week_start(date_from)
    while current <= date_to:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


def _months_in_range(date_from: date, date_to: date) -> list[tuple[int, int]]:
    months, year, month = [], date_from.year, date_from.month
    while (year, month) <= (date_to.year, date_to.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _level(value: float | None, thresholds: tuple[float, float, float], higher_is_better: bool = False) -> str | None:
    """Map ``value`` to Elite/High/Medium/Low using three ascending thresholds."""
    if value is None:
        return None
    elite, high, medium = thresholds
    if higher_is_better:
        return "Elite" if value >= elite else "High" if value >= high else "Medium" if value >= medium else "Low"
    return "Elite" if value < elite else "High" if value < high else "Medium" if value < medium else "Low"


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass
class Person:
    key: str
    label: str
    role: str | None
    team: str | None
    login: str | None


def resolve_person(team_config: TeamConfig, email: str | None, name: str | None) -> Person | None:
    """Identify the person behind an Azure identity, preferring the configured GitHub login."""
    login = team_config.login_for_azure(email, name)
    if login:
        return Person(login, login, team_config.role_for(login), team_config.team_for(login), login)
    if not (email or name):
        return None
    return Person((email or name or "").lower(), name or email or "", None, None, None)


def list_people(db: Session, team_config: TeamConfig) -> list[dict[str, Any]]:
    """Configured team members plus anyone assigned work items, for the person filter."""
    people: dict[str, dict[str, Any]] = {}
    for member in team_config.members():
        people[member["login"].lower()] = {
            "key": member["login"],
            "label": member["login"],
            "role": member["role"],
            "team": member["team"],
            "azure": member["azure"],
        }
    rows = db.query(AzureWorkItem.assigned_to_email, AzureWorkItem.assigned_to).distinct().all()
    for email, name in rows:
        person = resolve_person(team_config, email, name)
        if person and person.key.lower() not in people:
            people[person.key.lower()] = {
                "key": person.key,
                "label": person.label,
                "role": person.role,
                "team": person.team,
                "azure": email,
            }
    return sorted(people.values(), key=lambda item: item["label"].lower())


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


@dataclass
class StoryFilters:
    date_from: date
    date_to: date
    tags: list[str] = field(default_factory=list)
    tag_mode: str = "any"  # any | all
    person: str | None = None
    team: str | None = None
    types: list[str] = field(default_factory=list)
    status: str = "completed"  # completed (closed in range) | all (also open items active in range)

    def describe(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "tags": self.tags,
            "tag_mode": self.tag_mode,
            "person": self.person,
            "team": self.team,
            "types": self.types,
            "status": self.status,
        }


def _linked_prs(db: Session) -> dict[int, list[dict[str, Any]]]:
    """Map Azure work item id -> PRs whose title mentions AB#<id> (Azure Boards GitHub links)."""
    links: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rows = (
        db.query(PullRequest.pr_number, PullRequest.title, PullRequest.merged_at, Repository.name, Developer.github_username)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .filter(PullRequest.title.ilike("%AB#%"))
        .all()
    )
    for number, title, merged_at, repo, author in rows:
        for match in set(AB_LINK.findall(title or "")):
            links[int(match)].append(
                {
                    "repository": repo,
                    "pr_number": number,
                    "author": author,
                    "merged_at": merged_at.isoformat() if merged_at else None,
                }
            )
    return links


def _story_dict(item: AzureWorkItem, person: Person | None, linked: list[dict[str, Any]]) -> dict[str, Any]:
    cycle_start = item.activated_date or item.created_date
    cycle = _hours(item.closed_date, cycle_start)
    lead = _hours(item.closed_date, item.created_date)
    return {
        "id": item.id,
        "project": item.project,
        "type": item.work_item_type,
        "title": item.title,
        "state": item.state,
        "assignee": person.label if person else None,
        "person_key": person.key if person else None,
        "role": person.role if person else None,
        "team": person.team if person else None,
        "tags": sorted((tag.tag for tag in item.tags), key=str.lower),
        "story_points": item.story_points,
        "created_date": item.created_date.isoformat() if item.created_date else None,
        "activated_date": item.activated_date.isoformat() if item.activated_date else None,
        "closed_date": item.closed_date.isoformat() if item.closed_date else None,
        "cycle_time_days": _round(cycle / 24 if cycle is not None else None),
        "lead_time_days": _round(lead / 24 if lead is not None else None),
        "linked_prs": linked,
        "url": item.url,
    }


def _matches_tags(item_tags: Iterable[str], wanted: list[str], mode: str) -> bool:
    if not wanted:
        return True
    have = {tag.lower() for tag in item_tags}
    wanted_lower = [tag.lower() for tag in wanted]
    return all(tag in have for tag in wanted_lower) if mode == "all" else any(tag in have for tag in wanted_lower)


def query_stories(db: Session, filters: StoryFilters, team_config: TeamConfig) -> list[dict[str, Any]]:
    start, end = _range_bounds(filters.date_from, filters.date_to)
    query = db.query(AzureWorkItem).options(selectinload(AzureWorkItem.tags))
    if filters.status == "all":
        query = query.filter(
            ((AzureWorkItem.closed_date >= start) & (AzureWorkItem.closed_date < end))
            | (
                AzureWorkItem.closed_date.is_(None)
                & (AzureWorkItem.created_date < end)
                & (AzureWorkItem.changed_date >= start)
            )
        )
    else:
        query = query.filter(AzureWorkItem.closed_date >= start, AzureWorkItem.closed_date < end)
    if filters.types:
        query = query.filter(AzureWorkItem.work_item_type.in_(filters.types))

    links = _linked_prs(db)
    stories = []
    for item in query.order_by(AzureWorkItem.closed_date.desc(), AzureWorkItem.id.desc()).all():
        if not _matches_tags((tag.tag for tag in item.tags), filters.tags, filters.tag_mode):
            continue
        person = resolve_person(team_config, item.assigned_to_email, item.assigned_to)
        if filters.person and (person is None or person.key.lower() != filters.person.lower()):
            continue
        if filters.team and (person is None or person.team != filters.team):
            continue
        if team_config.enabled and not filters.team and (person is None or person.team is None):
            continue
        stories.append(_story_dict(item, person, links.get(item.id, [])))
    return stories


def summarize_stories(stories: list[dict[str, Any]], filters: StoryFilters) -> dict[str, Any]:
    completed = [story for story in stories if story["closed_date"]]
    cycle = [story["cycle_time_days"] for story in completed if story["cycle_time_days"] is not None]
    lead = [story["lead_time_days"] for story in completed if story["lead_time_days"] is not None]
    weeks = max(((filters.date_to - filters.date_from).days + 1) / 7, 1)
    tag_counts = Counter(tag for story in stories for tag in story["tags"])
    return {
        "total": len(stories),
        "completed": len(completed),
        "open": len(stories) - len(completed),
        "story_points_completed": _round(sum(story["story_points"] or 0 for story in completed)),
        "median_cycle_time_days": _round(_median(cycle)),
        "average_cycle_time_days": _round(mean(cycle) if cycle else None),
        "median_lead_time_days": _round(_median(lead)),
        "throughput_per_week": _round(len(completed) / weeks),
        "by_type": dict(Counter(story["type"] for story in stories).most_common()),
        "by_tag": dict(tag_counts.most_common(20)),
        "linked_pr_count": sum(len(story["linked_prs"]) for story in stories),
    }


def people_efficiency(
    db: Session, stories: list[dict[str, Any]], filters: StoryFilters, team_config: TeamConfig
) -> list[dict[str, Any]]:
    """Per-person delivery in the date range: stories finished, points, cycle time, bugs reported, PRs merged."""
    start, end = _range_bounds(filters.date_from, filters.date_to)
    weeks = max(((filters.date_to - filters.date_from).days + 1) / 7, 1)
    rows: dict[str, dict[str, Any]] = {}

    def row_for(person: Person) -> dict[str, Any]:
        if person.key not in rows:
            rows[person.key] = {
                "person_key": person.key,
                "person": person.label,
                "role": person.role,
                "team": person.team,
                "completed": 0,
                "story_points": 0.0,
                "cycle_times": [],
                "bugs_reported": 0,
                "prs_merged": 0,
            }
        return rows[person.key]

    for story in stories:
        if not story["closed_date"] or not story["person_key"]:
            continue
        row = row_for(Person(story["person_key"], story["assignee"], story["role"], story["team"], None))
        row["completed"] += 1
        row["story_points"] += story["story_points"] or 0
        if story["cycle_time_days"] is not None:
            row["cycle_times"].append(story["cycle_time_days"])

    # Bugs reported in the range (testers' main output).
    bugs = (
        db.query(AzureWorkItem.created_by_email, AzureWorkItem.created_by)
        .filter(AzureWorkItem.work_item_type == "Bug", AzureWorkItem.created_date >= start, AzureWorkItem.created_date < end)
        .all()
    )
    for email, name in bugs:
        person = resolve_person(team_config, email, name)
        if person is None or (filters.team and person.team != filters.team):
            continue
        if filters.person and person.key.lower() != filters.person.lower():
            continue
        if team_config.enabled and person.team is None:
            continue
        row_for(person)["bugs_reported"] += 1

    # GitHub PRs merged in the range, for people mapped to a GitHub login.
    merged = (
        db.query(Developer.github_username)
        .join(PullRequest, PullRequest.developer_id == Developer.id)
        .filter(PullRequest.merged_at >= start, PullRequest.merged_at < end)
        .all()
    )
    merged_by_login = Counter(login.lower() for (login,) in merged)
    for row in rows.values():
        row["prs_merged"] = merged_by_login.get(row["person_key"].lower(), 0)

    total_completed = sum(row["completed"] for row in rows.values()) or 1
    result = []
    for row in rows.values():
        cycle = row.pop("cycle_times")
        row["story_points"] = _round(row["story_points"])
        row["median_cycle_time_days"] = _round(_median(cycle))
        row["throughput_per_week"] = _round(row["completed"] / weeks)
        row["share_of_completed_pct"] = _round(row["completed"] / total_completed * 100, 1)
        result.append(row)
    return sorted(result, key=lambda item: (-item["completed"], -item["bugs_reported"], item["person"].lower()))


def list_tags(db: Session) -> list[dict[str, Any]]:
    rows: Counter[str] = Counter()
    for (tag,) in db.query(AzureWorkItemTag.tag).all():
        rows[tag] += 1
    return [{"tag": tag, "count": count} for tag, count in sorted(rows.items(), key=lambda item: item[0].lower())]


# ---------------------------------------------------------------------------
# DORA
# ---------------------------------------------------------------------------


def _is_success(run: AzurePipelineRun) -> bool:
    return (run.result or "").lower() in SUCCESS_RESULTS


def _is_failure(run: AzurePipelineRun) -> bool:
    return (run.result or "").lower() in FAILURE_RESULTS


def _production_deployments(db: Session, config: DevOpsConfig, since: datetime) -> list[AzurePipelineRun]:
    runs = (
        db.query(AzurePipelineRun)
        .filter(AzurePipelineRun.finish_time >= since)
        .order_by(AzurePipelineRun.finish_time)
        .all()
    )
    return [
        run
        for run in runs
        if config.is_deployment(run.definition_name)
        and config.is_production(run.definition_name)
        and (_is_success(run) or _is_failure(run))
    ]


def _first_commit_times(db: Session, pr_ids: list[int]) -> dict[int, datetime]:
    if not pr_ids:
        return {}
    times: dict[int, datetime] = {}
    for pr_id, committed_at in db.query(Commit.pr_id, Commit.committed_at).filter(Commit.pr_id.in_(pr_ids)).all():
        if committed_at and (pr_id not in times or committed_at < times[pr_id]):
            times[pr_id] = committed_at
    return times


def _deployment_frequency(successful: list[AzurePipelineRun], date_from: date, date_to: date) -> dict[str, Any]:
    days = {run.finish_time.date() for run in successful}
    weeks = _weeks_in_range(date_from, date_to)
    months = _months_in_range(date_from, date_to)
    per_week = [sum(1 for day in days if _week_start(day) == week) for week in weeks]
    per_month = [sum(1 for day in days if (day.year, day.month) == month) for month in months]
    median_week = _median([float(value) for value in per_week]) or 0.0
    median_month = _median([float(value) for value in per_month]) or 0.0
    span_weeks = max(((date_to - date_from).days + 1) / 7, 1)
    if not successful:
        level = None
    elif median_week >= 5:
        level = "Elite"
    elif median_week >= 1:
        level = "High"
    elif median_month >= 1:
        level = "Medium"
    else:
        level = "Low"
    return {
        "deployments": len(successful),
        "deployment_days": len(days),
        "deployments_per_week": _round(len(successful) / span_weeks),
        "median_deployment_days_per_week": _round(median_week),
        "median_deployment_days_per_month": _round(median_month),
        "level": level,
    }


def _incidents(db: Session, config: DevOpsConfig, since: datetime) -> list[AzureWorkItem]:
    items = (
        db.query(AzureWorkItem)
        .options(selectinload(AzureWorkItem.tags))
        .filter(AzureWorkItem.created_date >= since)
        .all()
    )
    return [item for item in items if config.is_incident(item.work_item_type, [tag.tag for tag in item.tags])]


def compute_dora(
    db: Session,
    config: DevOpsConfig,
    team_config: TeamConfig,
    date_from: date,
    date_to: date,
    person: str | None = None,
    team: str | None = None,
) -> dict[str, Any]:
    start, end = _range_bounds(date_from, date_to)
    deployments = _production_deployments(db, config, start - LOOKBACK_FOR_LINKING)
    successful_all = [run for run in deployments if _is_success(run)]

    # PRs (optionally scoped to a person / team) and the deployment that shipped each.
    pr_query = (
        db.query(PullRequest, Repository.name, Developer.github_username)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .filter(PullRequest.merged_at >= start - LOOKBACK_FOR_LINKING, PullRequest.merged_at < end)
    )
    prs = pr_query.all()
    if person:
        prs = [row for row in prs if row[2].lower() == person.lower()]
    elif team:
        prs = [row for row in prs if team_config.team_for(row[2]) == team]
    first_commits = _first_commit_times(db, [pr.id for pr, _, _ in prs])

    by_sha = {run.source_version.lower(): run for run in successful_all if run.source_version}
    by_repo: dict[str, list[AzurePipelineRun]] = defaultdict(list)
    attempts_by_repo: dict[str, list[AzurePipelineRun]] = defaultdict(list)
    for run in deployments:
        if run.repository_name:
            attempts_by_repo[run.repository_name.lower()].append(run)
            if _is_success(run):
                by_repo[run.repository_name.lower()].append(run)

    shipped: list[tuple[PullRequest, AzurePipelineRun, float]] = []
    first_attempts: list[AzurePipelineRun] = []
    for pr, repo_name, _ in prs:
        deployment = by_sha.get((pr.merge_commit_sha or "").lower())
        if deployment is None:
            deployment = next(
                (run for run in by_repo.get(repo_name.lower(), []) if run.finish_time >= pr.merged_at), None
            )
        attempt = next(
            (run for run in attempts_by_repo.get(repo_name.lower(), []) if run.finish_time >= pr.merged_at), None
        )
        if attempt is not None and start <= attempt.finish_time < end:
            first_attempts.append(attempt)
        if deployment is None or not (start <= deployment.finish_time < end):
            continue
        lead = _hours(deployment.finish_time, first_commits.get(pr.id) or pr.created_at)
        if lead is not None:
            shipped.append((pr, deployment, lead))

    scoped = bool(person or team)
    in_range = [run for run in deployments if start <= run.finish_time < end]
    if scoped:
        shipped_ids = {deployment.id for _, deployment, _ in shipped}
        successful = [run for run in successful_all if run.id in shipped_ids]
        attempts = list({run.id: run for run in first_attempts}.values())
    else:
        successful = [run for run in successful_all if start <= run.finish_time < end]
        attempts = in_range

    frequency = _deployment_frequency(successful, date_from, date_to)
    lead_times = [lead for _, _, lead in shipped]
    median_lead = _median(lead_times)

    # Change failure rate and recovery time.
    if config.incidents_enabled:
        method = "incidents"
        incidents = _incidents(db, config, start - LOOKBACK_FOR_LINKING)
        caused: dict[int, list[AzureWorkItem]] = defaultdict(list)
        for incident in incidents:
            if incident.created_date is None:
                continue
            previous = [run for run in successful_all if run.finish_time <= incident.created_date]
            if previous:
                caused[previous[-1].id].append(incident)
        failed_count = sum(1 for run in successful if caused.get(run.id))
        total = len(successful)
        recovery = [
            hours
            for run in successful
            for incident in caused.get(run.id, [])
            if (hours := _hours(incident.closed_date, run.finish_time)) is not None
        ]
        incident_count = sum(len(caused.get(run.id, [])) for run in successful)
    else:
        method = "pipeline"
        failed_runs = [run for run in attempts if _is_failure(run)]
        failed_count = len(failed_runs)
        total = len(attempts)
        recovery = []
        for failure in failed_runs:
            recovered = next(
                (
                    run
                    for run in successful_all
                    if run.definition_id == failure.definition_id and run.finish_time > failure.finish_time
                ),
                None,
            )
            hours = _hours(recovered.finish_time if recovered else None, failure.finish_time)
            if hours is not None:
                recovery.append(hours)
        incident_count = None

    rate = failed_count / total * 100 if total else None
    median_recovery = _median(recovery)

    weekly = []
    for week in _weeks_in_range(date_from, date_to):
        week_end = week + timedelta(days=7)
        week_success = [run for run in successful if week <= run.finish_time.date() < week_end]
        week_failed = [run for run in attempts if _is_failure(run) and week <= run.finish_time.date() < week_end]
        week_leads = [lead for _, deployment, lead in shipped if week <= deployment.finish_time.date() < week_end]
        weekly.append(
            {
                "week": _iso_week(week),
                "deployments": len(week_success),
                "failed_deployments": len(week_failed),
                "median_lead_time_hours": _round(_median(week_leads)),
            }
        )

    return {
        "scope": {"person": person, "team": team, "kind": "person" if person else "team" if team else "all"},
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "method": method,
        "deployment_frequency": frequency,
        "lead_time": {
            "median_hours": _round(median_lead),
            "average_hours": _round(mean(lead_times) if lead_times else None),
            "pull_requests": len(lead_times),
            "level": _level(median_lead, (24, 24 * 7, 24 * 30)),
        },
        "change_failure_rate": {
            "rate": _round(rate, 1),
            "failed": failed_count,
            "total": total,
            "incidents": incident_count,
            "level": _level(rate, (5.0001, 10.0001, 15.0001)),
        },
        "recovery_time": {
            "median_hours": _round(median_recovery),
            "samples": len(recovery),
            "level": _level(median_recovery, (1, 24, 24 * 7)),
        },
        "weekly": weekly,
        "benchmarks": BENCHMARKS,
        "pipelines": sorted({run.definition_name for run in in_range}),
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

REPORT_COLUMNS = [
    ("id", "ID"),
    ("type", "Type"),
    ("title", "Title"),
    ("state", "State"),
    ("assignee", "Assignee"),
    ("role", "Role"),
    ("team", "Team"),
    ("tags", "Tags"),
    ("story_points", "Story points"),
    ("created_date", "Created"),
    ("activated_date", "Activated"),
    ("closed_date", "Closed"),
    ("cycle_time_days", "Cycle time (days)"),
    ("lead_time_days", "Lead time (days)"),
    ("linked_prs", "Linked PRs"),
    ("url", "URL"),
]


def _cell(story: dict[str, Any], key: str) -> str:
    value = story.get(key)
    if key == "tags":
        return "; ".join(value or [])
    if key == "linked_prs":
        return "; ".join(f"{pr['repository']}#{pr['pr_number']}" for pr in value or [])
    if key.endswith("_date") and value:
        return str(value)[:10]
    return "" if value is None else str(value)


def stories_csv(stories: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in REPORT_COLUMNS])
    for story in stories:
        writer.writerow([_cell(story, key) for key, _ in REPORT_COLUMNS])
    return buffer.getvalue()


def _md(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _format_hours(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} days"


def markdown_report(
    filters: StoryFilters,
    summary: dict[str, Any],
    efficiency: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    dora: dict[str, Any] | None,
) -> str:
    applied = [f"**Date range:** {filters.date_from} to {filters.date_to} ({filters.status} items)"]
    if filters.tags:
        applied.append(f"**Tags ({filters.tag_mode}):** {', '.join(filters.tags)}")
    if filters.types:
        applied.append(f"**Types:** {', '.join(filters.types)}")
    if filters.team:
        applied.append(f"**Team:** {filters.team}")
    if filters.person:
        applied.append(f"**Person:** {filters.person}")

    lines = [
        "# DevOps Integration report",
        "",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        *[f"- {item}" for item in applied],
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Work items completed | {summary['completed']} |",
        f"| Story points completed | {summary['story_points_completed']} |",
        f"| Throughput | {summary['throughput_per_week']} per week |",
        f"| Median cycle time (active -> closed) | {_md(summary['median_cycle_time_days'])} days |",
        f"| Median lead time (created -> closed) | {_md(summary['median_lead_time_days'])} days |",
        f"| Open items in range | {summary['open']} |",
        f"| Linked GitHub PRs (AB#) | {summary['linked_pr_count']} |",
        "",
    ]
    if dora:
        frequency, lead, cfr, recovery = (
            dora["deployment_frequency"],
            dora["lead_time"],
            dora["change_failure_rate"],
            dora["recovery_time"],
        )
        lines += [
            "## DORA metrics",
            "",
            f"Change failure rate and recovery time are measured from **{dora['method']}**.",
            "",
            "| Metric | Value | Level |",
            "|---|---|---|",
            f"| Deployment frequency | {frequency['deployments']} deployments, {frequency['deployments_per_week']} per week | {_md(frequency['level'])} |",
            f"| Lead time for changes (median) | {_format_hours(lead['median_hours'])} over {lead['pull_requests']} PRs | {_md(lead['level'])} |",
            f"| Change failure rate | {_md(cfr['rate'])}% ({cfr['failed']} of {cfr['total']}) | {_md(cfr['level'])} |",
            f"| Failed deployment recovery time (median) | {_format_hours(recovery['median_hours'])} | {_md(recovery['level'])} |",
            "",
        ]
    lines += [
        "## Efficiency by person",
        "",
        "| Person | Role | Team | Completed | Story points | Median cycle (days) | Per week | Share | Bugs reported | PRs merged |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in efficiency:
        lines.append(
            f"| {_md(row['person'])} | {_md(row['role'])} | {_md(row['team'])} | {row['completed']} | "
            f"{_md(row['story_points'])} | {_md(row['median_cycle_time_days'])} | {_md(row['throughput_per_week'])} | "
            f"{_md(row['share_of_completed_pct'])}% | {row['bugs_reported']} | {row['prs_merged']} |"
        )
    lines += [
        "",
        f"## Work items ({len(stories)})",
        "",
        "| ID | Type | Title | State | Assignee | Tags | Points | Closed | Cycle (days) | PRs |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for story in stories:
        lines.append(
            f"| [{story['id']}]({story['url']}) | {_md(story['type'])} | {_md(story['title'])} | {_md(story['state'])} | "
            f"{_md(story['assignee'])} | {_md(', '.join(story['tags']))} | {_md(story['story_points'])} | "
            f"{_cell(story, 'closed_date')} | {_md(story['cycle_time_days'])} | {_md(_cell(story, 'linked_prs'))} |"
        )
    return "\n".join(lines) + "\n"
