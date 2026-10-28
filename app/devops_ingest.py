"""Sync Azure DevOps work items and pipeline runs into the database."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.azure_devops import AzureDevOpsClient
from app.devops_config import DevOpsConfig, load_devops_config
from app.models import AzurePipelineRun, AzureWorkItem, AzureWorkItemTag
from app.teams import SkippedAuthors, TeamConfig, load_team_config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_FRACTION = re.compile(r"\.(\d+)")


def parse_azure_datetime(value: str | None) -> datetime | None:
    """Parse Azure timestamps (up to 7 fractional digits, 'Z') into naive UTC."""
    if not value:
        return None
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), value.replace("Z", "+00:00"), count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@dataclass
class DevOpsSyncStats:
    work_items: int = 0
    pipeline_runs: int = 0
    skipped_work_items: int = 0
    azure_requests: int = 0
    projects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


WORK_ITEM_DATE_FIELDS = ("created_date", "activated_date", "resolved_date", "closed_date", "changed_date")
WORK_ITEM_FIELDS = (
    "project",
    "work_item_type",
    "title",
    "state",
    "assigned_to",
    "assigned_to_email",
    "created_by",
    "created_by_email",
    "story_points",
    "iteration_path",
    "area_path",
    "url",
)
RUN_DATE_FIELDS = ("queue_time", "start_time", "finish_time")
RUN_FIELDS = (
    "build_number",
    "definition_id",
    "definition_name",
    "status",
    "result",
    "reason",
    "source_branch",
    "source_version",
    "repository_name",
    "repository_type",
    "requested_for",
    "requested_for_email",
    "url",
)


def upsert_work_item(db: Session, data: dict) -> AzureWorkItem:
    item = db.get(AzureWorkItem, data["id"])
    if item is None:
        item = AzureWorkItem(id=data["id"])
        db.add(item)
    for name in WORK_ITEM_FIELDS:
        setattr(item, name, data.get(name))
    item.title = (data.get("title") or "")[:1024]
    for name in WORK_ITEM_DATE_FIELDS:
        value = data.get(name)
        setattr(item, name, parse_azure_datetime(value) if isinstance(value, str) else value)
    wanted = {tag.lower(): tag for tag in data.get("tags", [])}
    for tag in list(item.tags):
        if tag.tag.lower() not in wanted:
            item.tags.remove(tag)
        else:
            wanted.pop(tag.tag.lower())
    for tag in wanted.values():
        item.tags.append(AzureWorkItemTag(tag=tag[:255]))
    return item


def upsert_pipeline_run(db: Session, data: dict) -> AzurePipelineRun:
    run = db.query(AzurePipelineRun).filter_by(project=data["project"], build_id=data["build_id"]).one_or_none()
    if run is None:
        run = AzurePipelineRun(project=data["project"], build_id=data["build_id"])
        db.add(run)
    for name in RUN_FIELDS:
        setattr(run, name, data.get(name))
    run.definition_name = data.get("definition_name") or ""
    for name in RUN_DATE_FIELDS:
        value = data.get(name)
        setattr(run, name, parse_azure_datetime(value) if isinstance(value, str) else value)
    return run


def _belongs_to_team(data: dict, team_config: TeamConfig) -> bool:
    if not team_config.enabled:
        return True
    return bool(
        team_config.login_for_azure(data.get("assigned_to_email"), data.get("assigned_to"))
        or team_config.login_for_azure(data.get("created_by_email"), data.get("created_by"))
    )


def run_devops_ingestion(
    db: Session,
    lookback_days: int = 30,
    config_path: str = "config.yaml",
    devops_config: DevOpsConfig | None = None,
    client: AzureDevOpsClient | None = None,
) -> DevOpsSyncStats:
    """Fetch work items and pipeline runs changed in the last ``lookback_days`` and upsert them.

    With teams configured, work items neither assigned to nor created by a team
    member are skipped (and logged), like GitHub data from non-members.
    Pipeline runs are not person-specific and are always stored.
    """
    config = devops_config or load_devops_config(config_path)
    if not config.configured:
        raise RuntimeError("Azure DevOps is not configured: missing " + ", ".join(config.missing()))
    team_config = load_team_config(config_path)
    client = client or AzureDevOpsClient(config)
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    stats = DevOpsSyncStats(projects=list(config.projects))
    skipped = SkippedAuthors()

    for project in config.projects:
        logger.info("Fetching Azure DevOps work items and pipeline runs for %s.", project)
        for data in client.fetch_work_items(project, since):
            if not _belongs_to_team(data, team_config):
                skipped.record(data.get("assigned_to_email") or data.get("assigned_to"), "work_item", project)
                stats.skipped_work_items += 1
                continue
            upsert_work_item(db, data)
            stats.work_items += 1
        db.flush()
        for data in client.fetch_pipeline_runs(project, since):
            upsert_pipeline_run(db, data)
            stats.pipeline_runs += 1
        db.commit()

    stats.azure_requests = client.request_count
    skipped.log_summary()
    logger.info(
        "Azure DevOps sync complete: %d work item(s), %d pipeline run(s), %d API request(s).",
        stats.work_items,
        stats.pipeline_runs,
        stats.azure_requests,
    )
    return stats
