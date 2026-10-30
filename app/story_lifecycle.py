"""Story lifecycle: one row per user story with its timeline, code, people and quality flags.

Built from the Azure Boards state history (``azure_work_item_state_changes``),
the stage mapping in config.yaml (``app.lifecycle_config``) and the GitHub PRs
linked to the story or to its child items. See docs/story-lifecycle.md for
every definition.

Story <-> PR links come from (each link keeps its source):

* ``azure link``     the work item's "GitHub Pull Request" / "GitHub Commit" links in Azure Boards
* ``AB# in PR``      ``AB#<id>`` in the PR title or description
* ``AB# in commit``  ``AB#<id>`` in a commit message of the PR
* ``branch name``    a number in the PR's branch name that is a known work item id
                     (``feature/12345-checkout``), only for ids of stories/children in scope
* ``via Task #n``    any of the above on a child item, rolled up to the story
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable
from urllib.parse import unquote

from sqlalchemy.orm import Session, selectinload

from app.devops_metrics import StoryFilters, _matches_tags, resolve_person
from app.lifecycle_config import LifecycleConfig, Stage
from app.models import AzureWorkItem, Commit, Developer, PullRequest, Repository
from app.teams import TeamConfig
from app.time_window import utc_now_naive

AB_REFERENCE = re.compile(r"AB#(\d+)", re.IGNORECASE)
# A number of 3+ digits that is a whole branch-name token: feature/12345-checkout, 12345_fix, bugfix/AB12345.
BRANCH_ID = re.compile(r"(?:^|[/_\-])(?:ab|us|story|bug|task)?#?(\d{3,})(?=$|[/_\-])", re.IGNORECASE)
LINK_SOURCES = ("azure link", "AB# in PR", "AB# in commit", "branch name")


def _hours(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return max((later - earlier).total_seconds() / 3600, 0.0)


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _median(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return median(present) if present else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


# ---------------------------------------------------------------------------
# Story <-> pull request links
# ---------------------------------------------------------------------------


@dataclass
class LinkedPr:
    pr: PullRequest
    repository: str
    author: str
    sources: set[str]

    @property
    def status(self) -> str:
        if self.pr.merged_at is not None or (self.pr.state or "").upper() == "MERGED":
            return "merged"
        if (self.pr.state or "").upper() == "CLOSED":
            return "abandoned"
        if (self.pr.state or "").upper() == "OPEN":
            return "open"
        return "unknown"  # synced before PR states were recorded


def _artifact_id(url: str) -> str:
    """Last segment of a ``vstfs:///GitHub/PullRequest/<connection>%2F<id>`` artifact link."""
    return re.split(r"[/]", unquote(url))[-1]


def link_pull_requests(db: Session, items: list[AzureWorkItem]) -> dict[int, dict[int, LinkedPr]]:
    """Map work item id -> PR id -> link (with all the sources that link them)."""
    ids = {item.id for item in items}
    if not ids:
        return {}
    rows = (
        db.query(PullRequest, Repository.name, Developer.github_username)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .all()
    )
    prs = {pr.id: (pr, repo, author) for pr, repo, author in rows}
    by_github_id = {pr.github_id: pr.id for pr, _, _ in rows if pr.github_id}
    by_number: dict[int, list[int]] = defaultdict(list)
    for pr, _, _ in rows:
        by_number[pr.pr_number].append(pr.id)
    by_merge_sha = {pr.merge_commit_sha.lower(): pr.id for pr, _, _ in rows if pr.merge_commit_sha}

    links: dict[int, dict[int, LinkedPr]] = defaultdict(dict)

    def add(work_item_id: int, pr_id: int | None, source: str) -> None:
        if pr_id is None or work_item_id not in ids or pr_id not in prs:
            return
        pr, repo, author = prs[pr_id]
        link = links[work_item_id].setdefault(pr_id, LinkedPr(pr, repo, author, set()))
        link.sources.add(source)

    for pr, _, _ in rows:
        mentioned = {int(match) for match in AB_REFERENCE.findall(pr.title or "")}
        mentioned |= {int(value) for value in (pr.work_item_refs or "").split(",") if value.strip().isdigit()}
        for work_item_id in mentioned:
            add(work_item_id, pr.id, "AB# in PR")
        for match in BRANCH_ID.findall(pr.head_branch or ""):
            add(int(match), pr.id, "branch name")

    for pr_id, message in db.query(Commit.pr_id, Commit.message).filter(Commit.message.ilike("%AB#%")):
        for match in AB_REFERENCE.findall(message or ""):
            add(int(match), pr_id, "AB# in commit")

    pr_by_commit: dict[str, int] = {}
    if any((relation.name or "").lower() == "github commit" for item in items for relation in item.relations):
        pr_by_commit = {commit_hash.lower(): pr_id for pr_id, commit_hash in db.query(Commit.pr_id, Commit.commit_hash) if commit_hash}

    for item in items:
        for relation in item.relations:
            name = (relation.name or "").lower()
            if name == "github pull request":
                key = _artifact_id(relation.url)
                if not key.isdigit():
                    continue
                pr_id = by_github_id.get(int(key))
                if pr_id is None and len(by_number.get(int(key), [])) == 1:
                    pr_id = by_number[int(key)][0]
                add(item.id, pr_id, "azure link")
            elif name == "github commit":
                sha = _artifact_id(relation.url).lower()
                add(item.id, pr_by_commit.get(sha) or by_merge_sha.get(sha), "azure link")
    return links


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


@dataclass
class Interval:
    state: str | None
    stage: Stage | None
    start: datetime
    end: datetime | None
    assignee: tuple[str | None, str | None]


def _intervals(item: AzureWorkItem, now: datetime) -> tuple[list[Interval], list[dict[str, Any]]]:
    """State intervals (split on assignee changes) and the state transitions of ``item``."""
    history = sorted(item.history, key=lambda change: (change.changed_at, change.rev))
    if not history:
        # No downloaded history (e.g. synced before this feature): one interval in the current state.
        start = item.created_date or item.changed_date or now
        return [Interval(item.state, None, start, None, (item.assigned_to, item.assigned_to_email))], []
    intervals: list[Interval] = []
    transitions: list[dict[str, Any]] = []
    previous_state: str | None = None
    for index, change in enumerate(history):
        end = history[index + 1].changed_at if index + 1 < len(history) else None
        state = change.state if change.state is not None else previous_state
        intervals.append(Interval(state, None, change.changed_at, end, (change.assigned_to, change.assigned_to_email)))
        if state != previous_state:
            transitions.append(
                {
                    "from_state": previous_state,
                    "to_state": state,
                    "at": change.changed_at,
                    "by": change.changed_by,
                    "by_email": change.changed_by_email,
                }
            )
        previous_state = state
    return intervals, transitions


# ---------------------------------------------------------------------------
# One story
# ---------------------------------------------------------------------------


def _stage_name(config: LifecycleConfig, state: str | None) -> str | None:
    stage = config.stage_for(state)
    return stage.name if stage else None


def _person(team_config: TeamConfig, email: str | None, name: str | None) -> tuple[str, str, str | None, str | None] | None:
    person = resolve_person(team_config, email, name)
    if person is None:
        return None
    return person.key, person.label, person.team, person.role


def _github_person(team_config: TeamConfig, login: str) -> tuple[str, str, str | None, str | None]:
    member = team_config.member(login)
    if member is not None:
        return member.key, member.display_name, member.team, member.role
    return login, login, team_config.team_for(login), None


def build_lifecycle(
    item: AzureWorkItem,
    config: LifecycleConfig,
    team_config: TeamConfig,
    prs: dict[int, LinkedPr],
    children: list[AzureWorkItem],
    related_bugs: list[AzureWorkItem],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now_naive()
    intervals, transitions = _intervals(item, now)
    for interval in intervals:
        interval.stage = config.stage_for(interval.state)
    final_stage = config.stage_for(item.state) or (intervals[-1].stage if intervals else None)
    created = item.created_date or (intervals[0].start if intervals else None)

    # Stage entries (a move between two states of the same stage is not a new entry).
    entries: list[tuple[Stage | None, str | None, datetime, str | None]] = []
    for transition in transitions:
        stage = config.stage_for(transition["to_state"])
        previous = entries[-1][0] if entries else None
        if not entries or stage is None or previous is None or stage.name != previous.name:
            entries.append((stage, transition["to_state"], transition["at"], transition["by"]))
    first_entered: dict[str, datetime] = {}
    last_entered: dict[str, datetime] = {}
    entry_counts: Counter[str] = Counter()
    for stage, _, at, _ in entries:
        if stage is None:
            continue
        first_entered.setdefault(stage.name, at)
        last_entered[stage.name] = at
        entry_counts[stage.name] += 1

    released_names = [stage.name for stage in config.stages if stage.kind == "released"]
    closed_names = [stage.name for stage in config.stages if stage.kind == "closed"]
    ever_released = [first_entered[name] for name in released_names if name in first_entered]
    final_kind = final_stage.kind if final_stage else None
    if ever_released and final_kind in ("released", "closed"):
        outcome = "released"
        released_at = max(last_entered[name] for name in released_names if name in last_entered)
        finished_at = released_at
    elif final_kind == "closed":
        outcome = "closed_unreleased"
        released_at = None
        finished_at = max((last_entered[name] for name in closed_names if name in last_entered), default=item.closed_date)
    else:
        outcome = "in_progress"
        released_at = None
        finished_at = None
    closed_at = max((last_entered[name] for name in closed_names if name in last_entered), default=None)

    dev = config.dev_stage
    work_stage_names = [stage.name for stage in config.stages if stage.kind == "work"]
    dev_start = first_entered.get(dev.name) if dev else None
    if dev_start is None:
        dev_start = min((first_entered[name] for name in work_stage_names if name in first_entered), default=None)

    # Time per stage, and active (work) vs waiting time between dev start and finish (or now).
    stage_hours: dict[str, float] = defaultdict(float)
    unmapped: set[str] = set()
    work_hours = wait_hours = 0.0
    horizon = finished_at or now
    for interval in intervals:
        end = interval.end or (now if not (interval.stage and interval.stage.is_terminal) else interval.start)
        hours = _hours(end, interval.start) or 0.0
        if interval.stage is None:
            if interval.state:
                unmapped.add(interval.state)
            continue
        stage_hours[interval.stage.name] += hours
        if dev_start is None:
            continue
        start, stop = max(interval.start, dev_start), min(end, horizon)
        clipped = _hours(stop, start) if stop > start else 0.0
        if interval.stage.kind == "work":
            work_hours += clipped or 0.0
        elif interval.stage.kind == "wait":
            wait_hours += clipped or 0.0

    # Rework: moves back to an earlier stage.
    testing = {stage.name for stage in config.testing_stages}
    qa_bounces = reopened = rework = 0
    for (prev_stage, _, _, _), (stage, _, _, _) in zip(entries, entries[1:]):
        if prev_stage is None or stage is None or stage.order >= prev_stage.order or stage.is_terminal:
            continue
        if prev_stage.is_terminal:
            reopened += 1
            rework += 1
        elif prev_stage.name in testing:
            rework += 1
            if dev and stage.order <= dev.order:
                qa_bounces += 1
    repeated = sorted(name for name, count in entry_counts.items() if count > 1)
    entered_work = [name for name in work_stage_names if name in first_entered]
    skipped = (
        [stage.name for stage in config.stages if stage.kind == "work" and stage.name not in first_entered]
        if outcome == "released"
        else []
    )
    closed_directly = outcome != "in_progress" and not entered_work

    # People.
    developers: dict[str, tuple[str, str | None, str | None]] = {}
    testers: dict[str, tuple[str, str | None, str | None]] = {}

    def add_person(person, default_group: dict) -> None:
        if person is None:
            return
        key, label, team, role = person
        group = testers if role == "tester" else developers if role == "developer" else default_group
        group[key] = (label, team, role)

    for interval in intervals:
        if interval.stage is None or not any(interval.assignee):
            continue
        if dev and interval.stage.name == dev.name:
            add_person(_person(team_config, interval.assignee[1], interval.assignee[0]), developers)
        elif interval.stage.name in testing and interval.stage.kind == "work":
            add_person(_person(team_config, interval.assignee[1], interval.assignee[0]), testers)
    for transition in transitions:
        from_stage = config.stage_for(transition["from_state"])
        if from_stage is not None and from_stage.name in testing and from_stage.kind == "work":
            add_person(_person(team_config, transition["by_email"], transition["by"]), testers)
    for link in prs.values():
        add_person(_github_person(team_config, link.author), developers)
    assignee = _person(team_config, item.assigned_to_email, item.assigned_to)

    # Code.
    links = sorted(prs.values(), key=lambda link: (link.pr.created_at or now))
    statuses = Counter(link.status for link in links)
    commit_times = [commit.committed_at for link in links for commit in link.pr.commits if commit.committed_at]
    first_code = min(commit_times + [link.pr.created_at for link in links if link.pr.created_at], default=None)
    merges = [link.pr.merged_at for link in links if link.pr.merged_at]
    last_merge = max(merges, default=None)
    code_hours = _hours(last_merge, first_code) if merges and statuses["open"] == 0 else None
    cycle_hours = _hours(finished_at, dev_start)
    lines_added = sum(link.pr.additions or 0 for link in links)
    lines_deleted = sum(link.pr.deletions or 0 for link in links)

    # Bugs: child bugs and related bugs; escaped = created after the release.
    bugs = {bug.id: bug for bug in [*children, *related_bugs] if (bug.work_item_type or "").lower() == "bug"}
    escaped = [bug for bug in bugs.values() if released_at and bug.created_date and bug.created_date > released_at]

    tags = sorted((tag.tag for tag in item.tags), key=str.lower)
    release_version = config.release_version(tags)
    flags = []
    if outcome != "in_progress" and not links:
        flags.append("closed_without_code")
    if outcome != "in_progress" and statuses["open"]:
        flags.append("open_pr_at_finish")
    if statuses["abandoned"]:
        flags.append("abandoned_pr")
    if skipped:
        flags.append("skipped_stages")
    if closed_directly:
        flags.append("closed_directly")
    if qa_bounces:
        flags.append("qa_bounce")
    if reopened:
        flags.append("reopened")
    if outcome == "released" and not release_version:
        flags.append("released_without_release_tag")
    if release_version and outcome != "released":
        flags.append("release_tag_not_released")
    if outcome == "closed_unreleased" and statuses["merged"]:
        flags.append("closed_unreleased_with_code")
    if unmapped:
        flags.append("unmapped_states")
    if not item.history:
        flags.append("no_history")

    timeline = [
        {
            "state": interval.state,
            "stage": interval.stage.name if interval.stage else None,
            "label": interval.stage.label if interval.stage else interval.state,
            "kind": interval.stage.kind if interval.stage else "other",
            "start": _iso(interval.start),
            "end": _iso(interval.end),
            "hours": _round(_hours(interval.end or (now if not (interval.stage and interval.stage.is_terminal) else interval.start), interval.start)),
            "assignee": interval.assignee[0] or interval.assignee[1],
        }
        for interval in intervals
    ]
    return {
        "id": item.id,
        "title": item.title,
        "type": item.work_item_type,
        "state": item.state,
        "stage": final_stage.name if final_stage else None,
        "outcome": outcome,
        "project": item.project,
        "area_path": item.area_path,
        "iteration_path": item.iteration_path,
        "tags": tags,
        "release_version": release_version,
        "story_points": item.story_points,
        "url": item.url,
        "assignee": assignee[1] if assignee else None,
        "assignee_key": assignee[0] if assignee else None,
        "team": assignee[2] if assignee else None,
        # Dates
        "created_at": _iso(created),
        "dev_start_at": _iso(dev_start),
        "stage_first_entered": {name: _iso(at) for name, at in first_entered.items()},
        "released_at": _iso(released_at),
        "closed_at": _iso(closed_at),
        "finished_at": _iso(finished_at),
        # Durations
        "lead_time_hours": _round(_hours(finished_at, created)),
        "cycle_time_hours": _round(cycle_hours),
        "age_hours": _round(_hours(now, created)) if outcome == "in_progress" else None,
        "stage_hours": {name: _round(hours) for name, hours in stage_hours.items()},
        "work_hours": _round(work_hours) if dev_start else None,
        "wait_hours": _round(wait_hours) if dev_start else None,
        "flow_efficiency": _round(work_hours / (work_hours + wait_hours), 3) if dev_start and work_hours + wait_hours else None,
        # Rework
        "rework_count": rework,
        "qa_bounces": qa_bounces,
        "reopened": reopened,
        "repeated_stages": repeated,
        "skipped_stages": skipped,
        "closed_directly": closed_directly,
        "unmapped_states": sorted(unmapped),
        # Code
        "pr_count": len(links),
        "prs_merged": statuses["merged"],
        "prs_open": statuses["open"],
        "prs_abandoned": statuses["abandoned"],
        "prs_unknown_state": statuses["unknown"],
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "changed_files": sum(link.pr.changed_files or 0 for link in links),
        "commits": sum(link.pr.commit_count or 0 for link in links),
        "review_comments": sum(link.pr.review_comments_count or 0 for link in links),
        "changes_requested": sum(link.pr.requested_changes_count or 0 for link in links),
        "first_code_at": _iso(first_code),
        "last_merge_at": _iso(last_merge),
        "code_time_hours": _round(code_hours),
        "code_share": _round(code_hours / cycle_hours, 3) if code_hours is not None and cycle_hours else None,
        "lines_per_point": _round((lines_added + lines_deleted) / item.story_points, 1) if item.story_points else None,
        "pull_requests": [
            {
                "repository": link.repository,
                "number": link.pr.pr_number,
                "title": link.pr.title,
                "status": link.status,
                "author": _github_person(team_config, link.author)[1],
                "base_branch": link.pr.base_branch,
                "head_branch": link.pr.head_branch,
                "additions": link.pr.additions,
                "deletions": link.pr.deletions,
                "review_comments": link.pr.review_comments_count,
                "changes_requested": link.pr.requested_changes_count,
                "created_at": _iso(link.pr.created_at),
                "merged_at": _iso(link.pr.merged_at),
                "sources": sorted(link.sources),
                "url": f"https://github.com/{link.repository}/pull/{link.pr.pr_number}",
            }
            for link in links
        ],
        # People
        "developers": [{"key": key, "name": value[0], "team": value[1]} for key, value in sorted(developers.items(), key=lambda kv: kv[1][0].lower())],
        "testers": [{"key": key, "name": value[0], "team": value[1]} for key, value in sorted(testers.items(), key=lambda kv: kv[1][0].lower())],
        # Bugs and testing
        "bugs_linked": len(bugs),
        "escaped_defects": len(escaped),
        "children": len(children),
        "testing": {"enabled": config.testing_enabled, "test_cases": None, "pass_rate": None, "coverage_pct": None},
        "flags": flags,
        "timeline": timeline,
        "transitions": [
            {
                "from_state": transition["from_state"],
                "to_state": transition["to_state"],
                "from_stage": _stage_name(config, transition["from_state"]),
                "to_stage": _stage_name(config, transition["to_state"]),
                "at": _iso(transition["at"]),
                "by": transition["by"],
            }
            for transition in transitions
        ],
    }


# ---------------------------------------------------------------------------
# All stories
# ---------------------------------------------------------------------------


def build_story_lifecycles(
    db: Session,
    filters: StoryFilters,
    team_config: TeamConfig,
    config: LifecycleConfig,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lifecycle rows for the stories matching ``filters``.

    ``status='completed'``: stories released or closed in the date range.
    ``status='all'``: also stories still in progress that existed during the range.
    """
    now = now or utc_now_naive()
    window = filters.bounds
    types = filters.types or config.story_types
    stories = (
        db.query(AzureWorkItem)
        .options(selectinload(AzureWorkItem.tags), selectinload(AzureWorkItem.history), selectinload(AzureWorkItem.relations))
        .filter(AzureWorkItem.work_item_type.in_(types), AzureWorkItem.created_date < window.end)
        .all()
    )
    story_ids = [story.id for story in stories]
    children = (
        db.query(AzureWorkItem).options(selectinload(AzureWorkItem.relations)).filter(AzureWorkItem.parent_id.in_(story_ids)).all()
        if story_ids
        else []
    )
    related_ids = {
        relation.target_id
        for story in stories
        for relation in story.relations
        if relation.target_id and (relation.name or "").lower() == "related"
    }
    related = {item.id: item for item in db.query(AzureWorkItem).filter(AzureWorkItem.id.in_(related_ids)).all()} if related_ids else {}
    children_by_parent: dict[int, list[AzureWorkItem]] = defaultdict(list)
    for child in children:
        children_by_parent[child.parent_id].append(child)

    links = link_pull_requests(db, [*stories, *children])
    rows = []
    for story in stories:
        story_prs = dict(links.get(story.id, {}))
        for child in children_by_parent.get(story.id, []):
            for pr_id, link in links.get(child.id, {}).items():
                merged = story_prs.setdefault(pr_id, LinkedPr(link.pr, link.repository, link.author, set()))
                merged.sources |= {f"via {child.work_item_type} #{child.id}"}
        related_bugs = [
            related[relation.target_id]
            for relation in story.relations
            if relation.target_id in related and (relation.name or "").lower() == "related"
        ]
        row = build_lifecycle(story, config, team_config, story_prs, children_by_parent.get(story.id, []), related_bugs, now)
        if _keep(row, filters, team_config, window):
            rows.append(row)
    rows.sort(key=lambda row: (row["finished_at"] or "9999", row["id"]), reverse=True)
    return rows


def _keep(row: dict[str, Any], filters: StoryFilters, team_config: TeamConfig, window) -> bool:
    finished = datetime.fromisoformat(row["finished_at"][:-1]) if row["finished_at"] else None
    if filters.status == "all":
        if finished is not None and finished < window.start:
            return False
    elif finished is None or not window.contains(finished):
        return False
    if not _matches_tags(row["tags"], filters.tags, filters.tag_mode):
        return False
    people = [*row["developers"], *row["testers"]]
    if row["assignee_key"]:
        people.append({"key": row["assignee_key"], "team": row["team"]})
    if filters.person and not any(person["key"].lower() == filters.person.lower() for person in people):
        return False
    if filters.team and not any(person.get("team") == filters.team for person in people):
        return False
    return True


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def summarize_lifecycles(rows: list[dict[str, Any]], config: LifecycleConfig) -> dict[str, Any]:
    released = [row for row in rows if row["outcome"] == "released"]
    finished = [row for row in rows if row["outcome"] != "in_progress"]
    tested = [row for row in rows if any(name in row["stage_first_entered"] for name in (stage.name for stage in config.testing_stages))]
    lines = sum(row["lines_added"] + row["lines_deleted"] for row in released)
    flag_counts = Counter(flag for row in rows for flag in row["flags"])
    return {
        "stories": len(rows),
        "released": len(released),
        "closed_unreleased": sum(1 for row in rows if row["outcome"] == "closed_unreleased"),
        "in_progress": sum(1 for row in rows if row["outcome"] == "in_progress"),
        "median_lead_time_days": _round((_median(row["lead_time_hours"] for row in released) or 0) / 24) if released else None,
        "median_cycle_time_days": _round((_median(row["cycle_time_hours"] for row in released) or 0) / 24) if released else None,
        "median_flow_efficiency": _round(_median(row["flow_efficiency"] for row in released), 3),
        "median_code_share": _round(_median(row["code_share"] for row in released), 3),
        "first_time_right_pct": _rate(sum(1 for row in released if row["rework_count"] == 0), len(released)),
        "qa_bounce_rate_pct": _rate(sum(1 for row in tested if row["qa_bounces"]), len(tested)),
        "reopen_rate_pct": _rate(sum(1 for row in finished if row["reopened"]), len(finished)),
        "closed_without_code": flag_counts["closed_without_code"],
        "open_pr_at_finish": flag_counts["open_pr_at_finish"],
        "skipped_stages": flag_counts["skipped_stages"],
        "closed_directly": flag_counts["closed_directly"],
        "released_without_release_tag": flag_counts["released_without_release_tag"],
        "bugs_linked": sum(row["bugs_linked"] for row in rows),
        "escaped_defects": sum(row["escaped_defects"] for row in rows),
        "defects_per_kloc": _round(sum(row["bugs_linked"] for row in released) / (lines / 1000), 2) if lines else None,
        "median_lines_per_point": _round(_median(row["lines_per_point"] for row in released), 1),
        "median_stage_hours": {
            stage.name: _round(_median(row["stage_hours"].get(stage.name) for row in rows if stage.name in row["stage_hours"]))
            for stage in config.stages
            if not stage.is_terminal
        },
        "flags": dict(flag_counts.most_common()),
        "releases": dict(Counter(row["release_version"] for row in released if row["release_version"]).most_common()),
        "unmapped_states": sorted({state for row in rows for state in row["unmapped_states"]}),
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _csv(rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    # BOM so Excel opens UTF-8 correctly.
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue()


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return value


def lifecycle_csv(rows: list[dict[str, Any]], config: LifecycleConfig) -> str:
    stages = [stage for stage in config.stages]
    header = [
        "story_id", "title", "type", "state", "outcome", "team", "assignee", "story_points", "tags", "release_version",
        "created_at", "dev_start_at",
        *[f"{stage.name}_entered_at" for stage in stages],
        "released_at", "closed_at", "lead_time_hours", "cycle_time_hours", "age_hours",
        *[f"{stage.name}_hours" for stage in stages if not stage.is_terminal],
        "work_hours", "wait_hours", "flow_efficiency",
        "rework_count", "qa_bounces", "reopened", "repeated_stages", "skipped_stages", "closed_directly",
        "pr_count", "prs_merged", "prs_open", "prs_abandoned", "lines_added", "lines_deleted", "changed_files", "commits",
        "review_comments", "changes_requested", "first_code_at", "last_merge_at", "code_time_hours", "code_share",
        "lines_per_point", "developers", "testers", "bugs_linked", "escaped_defects",
        "test_cases", "test_pass_rate", "coverage_pct", "flags", "url",
    ]
    table = [header]
    for row in rows:
        table.append(
            [
                _cell(value)
                for value in [
                    row["id"], row["title"], row["type"], row["state"], row["outcome"], row["team"], row["assignee"],
                    row["story_points"], row["tags"], row["release_version"], row["created_at"], row["dev_start_at"],
                    *[row["stage_first_entered"].get(stage.name) for stage in stages],
                    row["released_at"], row["closed_at"], row["lead_time_hours"], row["cycle_time_hours"], row["age_hours"],
                    *[row["stage_hours"].get(stage.name, 0) for stage in stages if not stage.is_terminal],
                    row["work_hours"], row["wait_hours"], row["flow_efficiency"],
                    row["rework_count"], row["qa_bounces"], row["reopened"], row["repeated_stages"], row["skipped_stages"],
                    row["closed_directly"], row["pr_count"], row["prs_merged"], row["prs_open"], row["prs_abandoned"],
                    row["lines_added"], row["lines_deleted"], row["changed_files"], row["commits"], row["review_comments"],
                    row["changes_requested"], row["first_code_at"], row["last_merge_at"], row["code_time_hours"],
                    row["code_share"], row["lines_per_point"],
                    [person["name"] for person in row["developers"]], [person["name"] for person in row["testers"]],
                    row["bugs_linked"], row["escaped_defects"],
                    row["testing"]["test_cases"], row["testing"]["pass_rate"], row["testing"]["coverage_pct"],
                    row["flags"], row["url"],
                ]
            ]
        )
    return _csv(table)


def transitions_csv(rows: list[dict[str, Any]], config: LifecycleConfig) -> str:
    table = [["story_id", "title", "from_state", "to_state", "from_stage", "to_stage", "changed_at", "changed_by", "hours_in_previous_state", "is_backward"]]
    for row in rows:
        previous_at = None
        for transition in row["transitions"]:
            from_stage = config.stage(transition["from_stage"]) if transition["from_stage"] else None
            to_stage = config.stage(transition["to_stage"]) if transition["to_stage"] else None
            at = datetime.fromisoformat(transition["at"][:-1])
            backward = bool(from_stage and to_stage and to_stage.order < from_stage.order and not to_stage.is_terminal)
            table.append(
                [
                    _cell(value)
                    for value in [
                        row["id"], row["title"], transition["from_state"], transition["to_state"], transition["from_stage"],
                        transition["to_stage"], transition["at"], transition["by"],
                        _round(_hours(at, previous_at)) if previous_at else None, backward,
                    ]
                ]
            )
            previous_at = at
    return _csv(table)


def pull_requests_csv(rows: list[dict[str, Any]]) -> str:
    table = [["story_id", "story_title", "repository", "pr_number", "pr_title", "status", "author", "base_branch", "head_branch",
              "additions", "deletions", "review_comments", "changes_requested", "created_at", "merged_at", "link_sources", "url"]]
    for row in rows:
        for pr in row["pull_requests"]:
            table.append(
                [
                    _cell(value)
                    for value in [
                        row["id"], row["title"], pr["repository"], pr["number"], pr["title"], pr["status"], pr["author"],
                        pr["base_branch"], pr["head_branch"], pr["additions"], pr["deletions"], pr["review_comments"],
                        pr["changes_requested"], pr["created_at"], pr["merged_at"], pr["sources"], pr["url"],
                    ]
                ]
            )
    return _csv(table)
