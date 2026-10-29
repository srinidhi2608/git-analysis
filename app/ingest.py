"""Orchestrates GitHub data ingestion into the Postgres database.

Calls GitHubIngestionService to fetch pull-request data for all active
repositories, then upserts Developers, Repositories, PullRequests, Commits
and review comments into the database.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.github_ingestion import GitHubIngestionService
from app.models import Commit, Developer, PullRequest, PullRequestComment, Repository
from app.teams import TeamConfig, load_team_config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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


def _upsert_developer(db: Session, github_username: str, team_config: TeamConfig) -> Developer:
    dev = db.query(Developer).filter_by(github_username=github_username).first()
    if dev is None:
        dev = Developer(github_username=github_username, team_name=None)
        db.add(dev)
    if team_config.enabled:
        dev.team_name = team_config.team_for(github_username)
    db.flush()
    return dev


def sync_developer_teams(db: Session, team_config: TeamConfig) -> None:
    """Apply config.yaml team assignments to developers already in the database.

    Developers no longer listed in any team get ``team_name = NULL`` and are hidden
    from the dashboard while teams are configured.
    """
    if not team_config.enabled:
        return
    changed = 0
    for dev in db.query(Developer).all():
        team_name = team_config.team_for(dev.github_username)
        if dev.team_name != team_name:
            dev.team_name = team_name
            changed += 1
    db.commit()
    if changed:
        logger.info("Updated team assignment for %d developer(s) from config.yaml.", changed)


def _upsert_repository(db: Session, name: str) -> Repository:
    repo = db.query(Repository).filter_by(name=name).first()
    if repo is None:
        repo = Repository(name=name, is_active=True)
        db.add(repo)
        db.flush()
    return repo


@dataclass
class IngestionStats:
    """Outcome of one sync, reported by GET /api/ingest/status."""

    upserted: int = 0
    unchanged: int = 0
    github_requests: int = 0
    skipped_authors: int = 0
    full_refresh: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _known_updated_at(db: Session) -> dict[tuple[str, int], datetime]:
    rows = (
        db.query(Repository.name, PullRequest.pr_number, PullRequest.github_updated_at)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .filter(PullRequest.github_updated_at.is_not(None))
        .all()
    )
    return {(name, number): updated_at for name, number, updated_at in rows}


def run_ingestion(
    db: Session,
    lookback_days: int = 7,
    config_path: str = "config.yaml",
    full_refresh: bool = False,
) -> IngestionStats:
    """Fetch GitHub PR data and persist it to the database.

    When teams are configured in config.yaml, only data authored by team members
    is fetched and stored (see ``app.teams``). Unless ``full_refresh`` is set,
    PRs already stored with the same GitHub ``updatedAt`` are not downloaded
    again, which saves GitHub API requests.
    """
    team_config = load_team_config(config_path)
    sync_developer_teams(db, team_config)
    service = GitHubIngestionService(
        config_path=config_path,
        lookback_days=lookback_days,
        team_config=team_config,
        known_updated_at=None if full_refresh else _known_updated_at(db),
    )
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

            if not author or not team_config.is_member(author):
                logger.debug("PR #%s in %s has no team-member author; skipping.", pr_number, repo_name)
                savepoint.rollback()
                continue

            repo = _upsert_repository(db, repo_name)
            dev = _upsert_developer(db, author, team_config)

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
            github_updated_at = _parse_dt(pr_data.get("updated_at"))

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
                    github_updated_at=github_updated_at,
                    merge_commit_sha=pr_data.get("merge_commit_sha"),
                    base_branch=pr_data.get("base_branch"),
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
                existing.github_updated_at = github_updated_at
                existing.merge_commit_sha = pr_data.get("merge_commit_sha") or existing.merge_commit_sha
                existing.base_branch = pr_data.get("base_branch") or existing.base_branch

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
    return IngestionStats(
        upserted=upserted,
        unchanged=service.unchanged_count,
        github_requests=service.request_count,
        skipped_authors=len(service.skipped.as_dict()),
        full_refresh=full_refresh,
    )
