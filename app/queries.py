from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Commit, Developer, PullRequest, Repository


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def get_team_performance_metrics(db: Session, days: int = 30):
    """Return team-level metrics for pull requests created in the last N days."""
    since = _utc_now_naive() - timedelta(days=days)
    team_name = func.coalesce(Developer.team_name, "Unassigned").label("team_name")

    pr_aggregates = (
        select(
            team_name,
            func.avg(PullRequest.cycle_time_minutes).label("average_pr_cycle_time_minutes"),
            func.count(PullRequest.id).label("total_prs"),
        )
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(PullRequest.created_at >= since)
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
        .where(PullRequest.created_at >= since)
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


def get_all_developers(db: Session):
    """Return all known developers."""
    stmt = select(Developer.github_username, Developer.team_name).order_by(Developer.github_username)
    return db.execute(stmt).mappings().all()


def get_pr_cycle_time_by_developer(db: Session, days: int = 30):
    """Return average PR cycle time (hours) per developer for the last N days."""
    since = _utc_now_naive() - timedelta(days=days)
    stmt = (
        select(
            Developer.github_username.label("developer"),
            (func.avg(PullRequest.cycle_time_minutes) / 60).label("cycleTimeHours"),
        )
        .join(Developer, PullRequest.developer_id == Developer.id)
        .where(PullRequest.created_at >= since)
        .where(PullRequest.cycle_time_minutes.is_not(None))
        .group_by(Developer.github_username)
        .order_by(Developer.github_username)
    )
    rows = db.execute(stmt).mappings().all()
    return [
        {"developer": r["developer"], "cycleTimeHours": round(float(r["cycleTimeHours"]), 2)}
        for r in rows
    ]


def get_prs_per_week(db: Session, developer: str | None = None, weeks: int = 6):
    """Return merged PR count per ISO week label for the last N weeks."""
    since = _utc_now_naive() - timedelta(weeks=weeks)

    stmt = select(PullRequest.merged_at).where(
        PullRequest.merged_at >= since,
        PullRequest.merged_at.is_not(None),
    )

    if developer:
        stmt = stmt.join(Developer, PullRequest.developer_id == Developer.id).where(
            Developer.github_username == developer
        )

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

