"""Save and restore the ingested GitHub data as a snapshot file.

A snapshot is a gzipped JSON export of everything ingestion stores
(repositories, developers, pull requests, commits, review comments). Rows are
restored by their natural keys (repository name, developer login, repository +
PR number, PR + commit hash, comment id), so loading a snapshot into a database
that already has data upserts instead of duplicating.

With Docker Compose the snapshot directory is bind-mounted from ./snapshots, so
snapshots survive `docker compose down -v`, image rebuilds and moving the
project to another machine.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.devops_ingest import RUN_DATE_FIELDS, RUN_FIELDS, WORK_ITEM_DATE_FIELDS, WORK_ITEM_FIELDS, upsert_pipeline_run, upsert_work_item
from app.models import (
    AzurePipelineRun,
    AzureWorkItem,
    Commit,
    Developer,
    PullRequest,
    PullRequestComment,
    Repository,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Version 2 adds Azure DevOps work items and pipeline runs; version 1 still loads.
SNAPSHOT_FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = (1, 2)
TABLES = ("repositories", "developers", "pull_requests", "commits", "comments", "azure_work_items", "azure_pipeline_runs")
SNAPSHOT_PATTERN = "snapshot-*.json.gz"
_SAFE_NAME = re.compile(r"^snapshot-[0-9TZ-]+\.json\.gz$")

PULL_REQUEST_FIELDS = (
    "title",
    "user_story_id",
    "created_at",
    "merged_at",
    "cycle_time_minutes",
    "review_comments_count",
    "commit_count",
    "changed_files",
    "additions",
    "deletions",
    "review_count",
    "reviewers_count",
    "approvals_count",
    "requested_changes_count",
    "review_decision",
    "first_review_comment_at",
    "last_review_comment_at",
    "github_updated_at",
    "merge_commit_sha",
)
COMMENT_FIELDS = ("comment_type", "author_login", "body", "path", "review_state", "created_at")
DATETIME_FIELDS = {
    "created_at",
    "merged_at",
    "first_review_comment_at",
    "last_review_comment_at",
    "github_updated_at",
    "committed_at",
    *WORK_ITEM_DATE_FIELDS,
    *RUN_DATE_FIELDS,
}


def _dump_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _load_value(field: str, value: Any) -> Any:
    if field in DATETIME_FIELDS and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def list_snapshots(directory: str | Path) -> list[dict[str, Any]]:
    """Snapshots in ``directory``, newest first."""
    path = Path(directory)
    if not path.is_dir():
        return []
    files = sorted(path.glob(SNAPSHOT_PATTERN), key=lambda item: item.name, reverse=True)
    return [
        {
            "name": item.name,
            "size_bytes": item.stat().st_size,
            "created_at": datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat(),
        }
        for item in files
    ]


def resolve_snapshot(directory: str | Path, name: str | None = None) -> Path | None:
    """Path of the named snapshot (or the newest one); ``None`` if there is none."""
    if name is None:
        snapshots = list_snapshots(directory)
        return Path(directory) / snapshots[0]["name"] if snapshots else None
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"Invalid snapshot name: {name}")
    path = Path(directory) / name
    return path if path.is_file() else None


def export_data(db: Session) -> dict[str, Any]:
    repositories = {repo.id: repo for repo in db.query(Repository).all()}
    developers = {dev.id: dev for dev in db.query(Developer).all()}
    pull_requests = db.query(PullRequest).all()
    pr_keys = {pr.id: (repositories[pr.repo_id].name, pr.pr_number) for pr in pull_requests}

    return {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repositories": [{"name": repo.name, "is_active": repo.is_active} for repo in repositories.values()],
        "developers": [
            {"github_username": dev.github_username, "team_name": dev.team_name} for dev in developers.values()
        ],
        "pull_requests": [
            {
                "repository": repositories[pr.repo_id].name,
                "pr_number": pr.pr_number,
                "author": developers[pr.developer_id].github_username,
                **{field: _dump_value(getattr(pr, field)) for field in PULL_REQUEST_FIELDS},
            }
            for pr in pull_requests
        ],
        "commits": [
            {
                "repository": pr_keys[commit.pr_id][0],
                "pr_number": pr_keys[commit.pr_id][1],
                "author": developers[commit.developer_id].github_username,
                "commit_hash": commit.commit_hash,
                "message": commit.message,
                "committed_at": _dump_value(commit.committed_at),
            }
            for commit in db.query(Commit).all()
        ],
        "comments": [
            {
                "repository": pr_keys[comment.pr_id][0],
                "pr_number": pr_keys[comment.pr_id][1],
                "external_id": comment.external_id,
                **{field: _dump_value(getattr(comment, field)) for field in COMMENT_FIELDS},
            }
            for comment in db.query(PullRequestComment).all()
        ],
        "azure_work_items": [
            {
                "id": item.id,
                **{field: getattr(item, field) for field in WORK_ITEM_FIELDS},
                **{field: _dump_value(getattr(item, field)) for field in WORK_ITEM_DATE_FIELDS},
                "tags": [tag.tag for tag in item.tags],
            }
            for item in db.query(AzureWorkItem).all()
        ],
        "azure_pipeline_runs": [
            {
                "project": run.project,
                "build_id": run.build_id,
                **{field: getattr(run, field) for field in RUN_FIELDS},
                **{field: _dump_value(getattr(run, field)) for field in RUN_DATE_FIELDS},
            }
            for run in db.query(AzurePipelineRun).all()
        ],
    }


def save_snapshot(db: Session, directory: str | Path, keep: int = 5) -> dict[str, Any]:
    """Write a new snapshot file and prune all but the newest ``keep`` snapshots."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    data = export_data(db)
    name = f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json.gz"
    target = path / name
    tmp = path / f".{name}.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(data, handle)
    tmp.replace(target)

    for old in list_snapshots(path)[keep:]:
        (path / old["name"]).unlink(missing_ok=True)

    counts = {table: len(data[table]) for table in TABLES}
    logger.info("Saved snapshot %s (%s).", target, ", ".join(f"{v} {k}" for k, v in counts.items()))
    return {"name": name, "counts": counts}


def import_data(db: Session, data: dict[str, Any]) -> dict[str, int]:
    """Upsert snapshot ``data`` into the database by natural keys."""
    if data.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(f"Unsupported snapshot format version: {data.get('format_version')}")

    repositories = {repo.name: repo for repo in db.query(Repository).all()}
    for row in data.get("repositories", []):
        repo = repositories.get(row["name"])
        if repo is None:
            repo = Repository(name=row["name"], is_active=row.get("is_active", True))
            db.add(repo)
            repositories[repo.name] = repo
    developers = {dev.github_username: dev for dev in db.query(Developer).all()}
    for row in data.get("developers", []):
        dev = developers.get(row["github_username"])
        if dev is None:
            dev = Developer(github_username=row["github_username"])
            db.add(dev)
            developers[dev.github_username] = dev
        dev.team_name = row.get("team_name")
    db.flush()

    pull_requests = {
        (repo_name, pr.pr_number): pr
        for pr, repo_name in db.query(PullRequest, Repository.name).join(Repository, PullRequest.repo_id == Repository.id)
    }
    for row in data.get("pull_requests", []):
        key = (row["repository"], row["pr_number"])
        pr = pull_requests.get(key)
        if pr is None:
            pr = PullRequest(repo_id=repositories[row["repository"]].id, pr_number=row["pr_number"])
            db.add(pr)
            pull_requests[key] = pr
        pr.developer_id = developers[row["author"]].id
        for field in PULL_REQUEST_FIELDS:
            setattr(pr, field, _load_value(field, row.get(field)))
    db.flush()

    pr_ids = {key: pr.id for key, pr in pull_requests.items()}
    commits = {(commit.pr_id, commit.commit_hash): commit for commit in db.query(Commit).all()}
    for row in data.get("commits", []):
        pr_id = pr_ids[(row["repository"], row["pr_number"])]
        commit = commits.get((pr_id, row["commit_hash"]))
        if commit is None:
            commit = Commit(pr_id=pr_id, commit_hash=row["commit_hash"])
            db.add(commit)
            commits[(pr_id, row["commit_hash"])] = commit
        commit.developer_id = developers[row["author"]].id
        commit.message = row.get("message") or ""
        commit.committed_at = _load_value("committed_at", row.get("committed_at"))

    comments = {comment.external_id: comment for comment in db.query(PullRequestComment).all()}
    for row in data.get("comments", []):
        comment = comments.get(row["external_id"])
        if comment is None:
            comment = PullRequestComment(external_id=row["external_id"])
            db.add(comment)
            comments[row["external_id"]] = comment
        comment.pr_id = pr_ids[(row["repository"], row["pr_number"])]
        for field in COMMENT_FIELDS:
            setattr(comment, field, _load_value(field, row.get(field)))
        comment.body = comment.body or ""

    for row in data.get("azure_work_items", []):
        upsert_work_item(db, {**row, **{field: _load_value(field, row.get(field)) for field in WORK_ITEM_DATE_FIELDS}})
    for row in data.get("azure_pipeline_runs", []):
        upsert_pipeline_run(db, {**row, **{field: _load_value(field, row.get(field)) for field in RUN_DATE_FIELDS}})

    db.commit()
    return {table: len(data.get(table, [])) for table in TABLES}


def load_snapshot(db: Session, path: str | Path) -> dict[str, int]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    try:
        counts = import_data(db, data)
    except Exception:
        db.rollback()
        raise
    logger.info("Loaded snapshot %s (%s).", path, ", ".join(f"{v} {k}" for k, v in counts.items()))
    return counts
