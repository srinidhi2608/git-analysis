from datetime import datetime

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session

from app.models import Commit, Developer, PullRequest, Repository
from app.time_window import TimeWindow


def active_in_window(window: TimeWindow):
    """Filter clause: PRs created or merged inside ``window``."""
    return or_(
        and_(PullRequest.created_at >= window.start, PullRequest.created_at < window.end),
        and_(PullRequest.merged_at >= window.start, PullRequest.merged_at < window.end),
    )


def merged_in_window(window: TimeWindow):
    """Filter clause: PRs merged inside ``window``."""
    return and_(PullRequest.merged_at >= window.start, PullRequest.merged_at < window.end)


def in_branches(branches: list[str] | None):
    """Filter clause restricting PRs to target (base) ``branches`` (``None``/empty: no restriction)."""
    if not branches:
        return true()
    return PullRequest.base_branch.in_(branches)


def _in_teams(teams: list[str] | None):
    """Filter clause restricting developers to ``teams`` (``None`` means no restriction)."""
    if teams is None:
        return true()
    return Developer.team_name.in_(teams)


def get_pull_request_counts_by_user_story(db: Session):
    """Return the count of pull requests grouped by user story ID."""
    stmt = (
        select(
            PullRequest.user_story_id,
            func.count(PullRequest.id).label("pull_request_count"),
        )
        .where(PullRequest.user_story_id.is_not(None))
        .group_by(PullRequest.user_story_id)
        .order_by(PullRequest.user_story_id)
    )
    return db.execute(stmt).all()


def get_team_performance_metrics(
    db: Session,
    days: int = 30,
    teams: list[str] | None = None,
    branches: list[str] | None = None,
    window: TimeWindow | None = None,
):
    """Return team-level metrics for pull requests created or merged in ``window`` (default: last N days)."""
    window = window or TimeWindow.last_days(days)
    team_name = func.coalesce(Developer.team_name, "Unassigned").label("team_name")

    pr_aggregates = (
        select(
            team_name,
            func.avg(PullRequest.cycle_time_minutes).label("average_pr_cycle_time_minutes"),
            func.count(PullRequest.id).label("total_prs"),
        )
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(
            active_in_window(window),
            _in_teams(teams),
            in_branches(branches),
        )
        .group_by(team_name)
        .subquery()
    )

    commit_aggregates = (
        select(
            team_name,
            func.count(Commit.id).label("total_commits"),
        )
        .select_from(Commit)
        .join(PullRequest, Commit.pr_id == PullRequest.id)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(
            active_in_window(window),
            _in_teams(teams),
            in_branches(branches),
        )
        .group_by(team_name)
        .subquery()
    )

    stmt = (
        select(
            pr_aggregates.c.team_name,
            pr_aggregates.c.average_pr_cycle_time_minutes,
            func.coalesce(commit_aggregates.c.total_commits, 0).label("total_commits"),
            pr_aggregates.c.total_prs,
        )
        .outerjoin(commit_aggregates, pr_aggregates.c.team_name == commit_aggregates.c.team_name)
        .order_by(pr_aggregates.c.team_name)
    )

    return db.execute(stmt).mappings().all()


def get_developer_metrics(db: Session, github_username: str):
    """Return aggregate metrics for a single developer.

    Rework is approximated as additional commits beyond the first commit on
    each PR because the current schema does not store the timestamps required
    to calculate post-review commits exactly.
    """
    pr_aggregates = (
        select(
            PullRequest.developer_id.label("developer_id"),
            func.avg(PullRequest.cycle_time_minutes).label("average_pr_cycle_time_minutes"),
            func.coalesce(func.sum(PullRequest.review_comments_count), 0).label("comments_received"),
        )
        .group_by(PullRequest.developer_id)
        .subquery()
    )

    per_pr_rework = (
        select(
            PullRequest.developer_id.label("developer_id"),
            Commit.pr_id.label("pr_id"),
            (func.count(Commit.id) - 1).label("rework_count"),
        )
        .select_from(Commit)
        .join(PullRequest, Commit.pr_id == PullRequest.id)
        .group_by(PullRequest.developer_id, Commit.pr_id)
        .subquery()
    )

    commit_aggregates = (
        select(
            per_pr_rework.c.developer_id,
            func.coalesce(func.sum(per_pr_rework.c.rework_count), 0).label("rework_count"),
        )
        .group_by(per_pr_rework.c.developer_id)
        .subquery()
    )

    stmt = (
        select(
            Developer.github_username,
            Developer.team_name,
            pr_aggregates.c.average_pr_cycle_time_minutes,
            func.coalesce(commit_aggregates.c.rework_count, 0).label("rework_count"),
            func.coalesce(pr_aggregates.c.comments_received, 0).label("comments_received"),
        )
        .outerjoin(pr_aggregates, Developer.id == pr_aggregates.c.developer_id)
        .outerjoin(commit_aggregates, Developer.id == commit_aggregates.c.developer_id)
        .where(Developer.github_username == github_username)
    )

    return db.execute(stmt).mappings().one_or_none()


def get_all_developers(db: Session, teams: list[str] | None = None):
    """Return known developers, optionally restricted to ``teams``."""
    stmt = (
        select(Developer.github_username, Developer.team_name)
        .where(_in_teams(teams))
        .order_by(Developer.github_username)
    )
    return db.execute(stmt).mappings().all()


def get_pr_cycle_time_by_developer(
    db: Session,
    days: int = 30,
    teams: list[str] | None = None,
    branches: list[str] | None = None,
    window: TimeWindow | None = None,
):
    """Return average PR cycle time (hours) per developer for PRs merged in ``window`` (default: last N days)."""
    window = window or TimeWindow.last_days(days)
    stmt = (
        select(
            Developer.github_username.label("developer"),
            (func.avg(PullRequest.cycle_time_minutes) / 60).label("cycleTimeHours"),
        )
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(merged_in_window(window))
        .where(PullRequest.cycle_time_minutes.is_not(None))
        .where(_in_teams(teams), in_branches(branches))
        .group_by(Developer.github_username)
        .order_by(Developer.github_username)
    )
    rows = db.execute(stmt).mappings().all()
    return [
        {"developer": r["developer"], "cycleTimeHours": round(float(r["cycleTimeHours"]), 2)}
        for r in rows
    ]


def get_prs_per_week(
    db: Session,
    developer: str | None = None,
    days: int = 42,
    teams: list[str] | None = None,
    branches: list[str] | None = None,
    window: TimeWindow | None = None,
):
    """Return merged PR count per ISO week label for PRs merged in ``window`` (default: last N days)."""
    window = window or TimeWindow.last_days(days)

    stmt = (
        select(PullRequest.merged_at)
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(
            merged_in_window(window),
            _in_teams(teams),
            in_branches(branches),
        )
    )

    if developer:
        stmt = stmt.where(Developer.github_username == developer)

    rows = db.execute(stmt).scalars().all()

    # Bucket by ISO year + week to avoid year-boundary collisions
    week_counts: dict[str, int] = {}
    for merged_at in rows:
        if merged_at is None:
            continue
        if isinstance(merged_at, str):
            merged_at = datetime.fromisoformat(merged_at)
        iso = merged_at.isocalendar()
        label = f"{iso[0]}-W{iso[1]:02d}"
        week_counts[label] = week_counts.get(label, 0) + 1

    # Sort chronologically (year-week labels sort correctly as strings)
    sorted_weeks = sorted(week_counts.items())
    return [{"week": w, "mergedPrs": c} for w, c in sorted_weeks]


def get_target_branches(
    db: Session,
    days: int | None = None,
    teams: list[str] | None = None,
    window: TimeWindow | None = None,
) -> dict:
    """Target (base) branches of stored PRs with PR counts, most used first.

    ``unknown`` counts PRs synced before the base branch was recorded (a GitHub
    sync with "Full refresh" fills it in).
    """
    stmt = (
        select(PullRequest.base_branch, func.count(PullRequest.id))
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(_in_teams(teams))
        .group_by(PullRequest.base_branch)
    )
    if window is None and days:
        window = TimeWindow.last_days(days)
    if window is not None:
        stmt = stmt.where(active_in_window(window))
    branches, unknown = [], 0
    for branch, count in db.execute(stmt).all():
        if branch is None:
            unknown += count
        else:
            branches.append({"name": branch, "pull_requests": count})
    branches.sort(key=lambda item: (-item["pull_requests"], item["name"]))
    return {"branches": branches, "unknown": unknown}
