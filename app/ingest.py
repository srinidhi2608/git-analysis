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
from app.models import Commit, Developer, PullRequest, PullRequestComment, Repository

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Store as naive UTC in the DB (matches existing model column type DateTime)
    return dt.replace(tzinfo=None)


def _upsert_commit(
    db: Session,
    pull_request: PullRequest,
    developer: Developer,
    commit_data: dict,
) -> None:
    commit_hash = commit_data.get("commit_hash")
    if not commit_hash:
        return

    existing = (
        db.query(Commit)
        .filter_by(pr_id=pull_request.id, commit_hash=commit_hash)
        .first()
    )
    committed_at = _parse_dt(commit_data.get("committed_at"))
    if existing is None:
        db.add(
            Commit(
                pr_id=pull_request.id,
                developer_id=developer.id,
                commit_hash=commit_hash,
                message=commit_data.get("message", ""),
                committed_at=committed_at,
            )
        )
        return

    existing.message = commit_data.get("message", existing.message)
    existing.committed_at = committed_at


def _upsert_comment(db: Session, pull_request: PullRequest, comment_data: dict) -> None:
    external_id = comment_data.get("external_id")
    if not external_id:
        return

    existing = db.query(PullRequestComment).filter_by(external_id=external_id).first()
    created_at = _parse_dt(comment_data.get("created_at"))
    if existing is None:
        db.add(
            PullRequestComment(
                pr_id=pull_request.id,
                external_id=external_id,
                comment_type=comment_data.get("comment_type", "review_comment"),
                author_login=comment_data.get("author_login"),
                body=comment_data.get("body", ""),
                path=comment_data.get("path"),
                review_state=comment_data.get("review_state"),
                created_at=created_at,
            )
        )
        return

    existing.comment_type = comment_data.get("comment_type", existing.comment_type)
    existing.author_login = comment_data.get("author_login", existing.author_login)
    existing.body = comment_data.get("body", existing.body)
    existing.path = comment_data.get("path", existing.path)
    existing.review_state = comment_data.get("review_state", existing.review_state)
    existing.created_at = created_at


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


def run_ingestion(db: Session, lookback_days: int = 7) -> int:
    """Fetch GitHub PR data and persist it to the database.

    Returns
    -------
    int
        Number of pull requests upserted.
    """
    service = GitHubIngestionService(lookback_days=lookback_days)
    prs = service.fetch_all()
    logger.info("Fetched %d pull requests from GitHub.", len(prs))

    upserted = 0
    for pr_data in prs:
        # Use a savepoint so a failure on one PR does not corrupt the session
        savepoint = db.begin_nested()
        try:
            repo_name: str = pr_data["repository"]
            pr_number: int = pr_data["pr_number"]
            author: str | None = pr_data.get("author")

            if not author:
                logger.debug("PR #%s in %s has no author; skipping.", pr_number, repo_name)
                savepoint.rollback()
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
            commit_count: int = pr_data.get("commit_count", 0) or 0
            changed_files: int = pr_data.get("changed_files", 0) or 0
            additions: int = pr_data.get("additions", 0) or 0
            deletions: int = pr_data.get("deletions", 0) or 0
            review_count: int = pr_data.get("review_count", 0) or 0
            reviewers_count: int = pr_data.get("reviewers_count", 0) or 0
            approvals_count: int = pr_data.get("approvals_count", 0) or 0
            requested_changes_count: int = pr_data.get("requested_changes_count", 0) or 0
            first_review_comment_at = _parse_dt(pr_data.get("first_review_comment_at"))
            last_review_comment_at = _parse_dt(pr_data.get("last_review_comment_at"))

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
                    commit_count=commit_count,
                    changed_files=changed_files,
                    additions=additions,
                    deletions=deletions,
                    review_count=review_count,
                    reviewers_count=reviewers_count,
                    approvals_count=approvals_count,
                    requested_changes_count=requested_changes_count,
                    review_decision=pr_data.get("review_decision"),
                    first_review_comment_at=first_review_comment_at,
                    last_review_comment_at=last_review_comment_at,
                )
                db.add(pull_request)
                db.flush()
            else:
                pull_request = existing
                existing.developer_id = dev.id
                existing.title = pr_data.get("title", existing.title)
                existing.created_at = created_at or existing.created_at
                existing.merged_at = merged_at
                existing.cycle_time_minutes = cycle_time_minutes
                existing.review_comments_count = review_comments_count
                existing.commit_count = commit_count
                existing.changed_files = changed_files
                existing.additions = additions
                existing.deletions = deletions
                existing.review_count = review_count
                existing.reviewers_count = reviewers_count
                existing.approvals_count = approvals_count
                existing.requested_changes_count = requested_changes_count
                existing.review_decision = pr_data.get("review_decision")
                existing.first_review_comment_at = first_review_comment_at
                existing.last_review_comment_at = last_review_comment_at

            for commit_data in pr_data.get("commits", []):
                _upsert_commit(db, pull_request, dev, commit_data)

            for comment_data in pr_data.get("comments", []):
                _upsert_comment(db, pull_request, comment_data)

            savepoint.commit()
            upserted += 1
        except Exception:
            savepoint.rollback()
            logger.exception("Failed to persist PR %s from %s.", pr_data.get("pr_number"), pr_data.get("repository"))

    db.commit()
    logger.info("Ingestion complete. %d pull requests upserted.", upserted)
    return upserted
