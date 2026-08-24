"""Orchestrates GitHub data ingestion into the Postgres database.

Calls GitHubIngestionService to fetch pull-request data for all active
repositories, then upserts Developers, Repositories, PullRequests, and
synthetic Commits into the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.github_ingestion import GitHubIngestionService
from app.models import Developer, PullRequest, Repository

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Store as naive UTC in the DB (matches existing model column type DateTime)
    return dt.replace(tzinfo=None)


def _upsert_developer(db: Session, github_username: str) -> Developer:
    dev = db.query(Developer).filter_by(github_username=github_username).first()
    if dev is None:
        dev = Developer(github_username=github_username, team_name=None)
        db.add(dev)
        db.flush()
    return dev


def _upsert_repository(db: Session, name: str) -> Repository:
    repo = db.query(Repository).filter_by(name=name).first()
    if repo is None:
        repo = Repository(name=name, is_active=True)
        db.add(repo)
        db.flush()
    return repo


def run_ingestion(db: Session) -> int:
    """Fetch GitHub PR data and persist it to the database.

    Returns
    -------
    int
        Number of pull requests upserted.
    """
    service = GitHubIngestionService()
    prs = service.fetch_all()
    logger.info("Fetched %d pull requests from GitHub.", len(prs))

    upserted = 0
    for pr_data in prs:
        try:
            repo_name: str = pr_data["repository"]
            pr_number: int = pr_data["pr_number"]
            author: str | None = pr_data.get("author")

            if not author:
                logger.debug("PR #%s in %s has no author; skipping.", pr_number, repo_name)
                continue

            repo = _upsert_repository(db, repo_name)
            dev = _upsert_developer(db, author)

            created_at = _parse_dt(pr_data.get("created_at"))
            merged_at = _parse_dt(pr_data.get("merged_at"))

            # Cycle time in minutes
            cycle_time_minutes: float | None = None
            if created_at and merged_at and merged_at > created_at:
                delta = merged_at - created_at
                cycle_time_minutes = round(delta.total_seconds() / 60, 2)

            existing = (
                db.query(PullRequest)
                .filter_by(repo_id=repo.id, pr_number=pr_number)
                .first()
            )

            review_comments_count: int = pr_data.get("review_comments_count", 0) or 0

            if existing is None:
                pull_request = PullRequest(
                    repo_id=repo.id,
                    developer_id=dev.id,
                    pr_number=pr_number,
                    title=pr_data.get("title", ""),
                    created_at=created_at,
                    merged_at=merged_at,
                    cycle_time_minutes=cycle_time_minutes,
                    review_comments_count=review_comments_count,
                )
                db.add(pull_request)
            else:
                existing.developer_id = dev.id
                existing.title = pr_data.get("title", existing.title)
                existing.created_at = created_at or existing.created_at
                existing.merged_at = merged_at
                existing.cycle_time_minutes = cycle_time_minutes
                existing.review_comments_count = review_comments_count

            upserted += 1
        except Exception:
            logger.exception("Failed to persist PR %s from %s.", pr_data.get("pr_number"), pr_data.get("repository"))

    db.commit()
    logger.info("Ingestion complete. %d pull requests upserted.", upserted)
    return upserted
