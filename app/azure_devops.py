"""Azure DevOps REST client: Boards work items and Pipelines runs.

Uses the REST API (api-version 7.1) with a personal access token (PAT):

* Work items: a WIQL query returns the ids changed since a date, then
  ``workitemsbatch`` loads their fields 200 at a time.
* Pipelines: ``_apis/build/builds`` lists completed runs finished since a date,
  following ``x-ms-continuationtoken`` paging.

Required PAT scopes: Work Items (Read), Build (Read).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from app.devops_config import DevOpsConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

API_VERSION = "7.1"
WORK_ITEM_BATCH_SIZE = 200
BUILD_PAGE_SIZE = 500
WORK_ITEM_FIELDS = [
    "System.Id",
    "System.TeamProject",
    "System.WorkItemType",
    "System.Title",
    "System.State",
    "System.AssignedTo",
    "System.CreatedBy",
    "System.CreatedDate",
    "System.ChangedDate",
    "System.Tags",
    "System.IterationPath",
    "System.AreaPath",
    "Microsoft.VSTS.Common.ActivatedDate",
    "Microsoft.VSTS.Common.ResolvedDate",
    "Microsoft.VSTS.Common.ClosedDate",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Scheduling.Effort",
]


class AzureDevOpsError(RuntimeError):
    """Raised for authentication, permission or API errors."""


def _identity(value: Any) -> tuple[str | None, str | None]:
    """(display name, email/unique name) from an Azure identity field."""
    if isinstance(value, dict):
        return value.get("displayName"), (value.get("uniqueName") or "").lower() or None
    if isinstance(value, str) and value:
        # Older responses: "Display Name <user@company.com>"
        if "<" in value and value.endswith(">"):
            name, _, email = value[:-1].partition("<")
            return name.strip() or None, email.strip().lower() or None
        return value, None
    return None, None


def _date(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _wiql_time(value: datetime) -> str:
    """ISO UTC timestamp for WIQL and the Builds API (naive datetimes are UTC)."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    seen: dict[str, str] = {}
    for tag in value.split(";"):
        tag = tag.strip()
        if tag and tag.lower() not in seen:
            seen[tag.lower()] = tag
    return list(seen.values())


class AzureDevOpsClient:
    def __init__(self, config: DevOpsConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.base_url = config.organization_url
        self.request_count = 0
        self._session = session or requests.Session()
        self._session.auth = ("", config.pat)
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        params = {"api-version": API_VERSION, **(kwargs.pop("params", None) or {})}
        for attempt in range(1, 4):
            self.request_count += 1
            response = self._session.request(method, url, params=params, timeout=60, **kwargs)
            if response.status_code == 429 and attempt < 3:
                wait = min(int(response.headers.get("Retry-After", "10") or 10), 120)
                logger.warning("Azure DevOps rate limit (429); waiting %d seconds.", wait)
                time.sleep(wait)
                continue
            # An invalid or expired PAT gets a 203 sign-in page (HTML) instead of JSON.
            if response.status_code in (401, 203) or (
                response.ok and "json" not in response.headers.get("Content-Type", "")
            ):
                raise AzureDevOpsError(
                    "Azure DevOps rejected the credentials. Check AZURE_DEVOPS_PAT (expired? missing "
                    "'Work Items: Read' / 'Build: Read' scopes?) and azure_devops.organization."
                )
            if response.status_code == 403:
                raise AzureDevOpsError(f"Azure DevOps denied access to {url}: {response.text[:200]}")
            if response.status_code == 404:
                raise AzureDevOpsError(f"Azure DevOps resource not found (check the project name): {url}")
            response.raise_for_status()
            return response
        raise AzureDevOpsError("Azure DevOps kept rate limiting the requests.")

    def _project_url(self, project: str, path: str) -> str:
        return f"{self.base_url}/{quote(project, safe='')}/_apis/{path}"

    # ------------------------------------------------------------------
    # Work items
    # ------------------------------------------------------------------

    def fetch_work_items(self, project: str, since: datetime, until: datetime | None = None) -> list[dict[str, Any]]:
        """Work items of the configured types changed in ``[since, until)`` (normalized dicts)."""
        types = ", ".join("'" + item.replace("'", "''") + "'" for item in self.config.work_item_types)
        until_clause = f"AND [System.ChangedDate] < '{_wiql_time(until)}' " if until else ""
        wiql = (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.TeamProject] = @project "
            f"AND [System.WorkItemType] IN ({types}) "
            f"AND [System.ChangedDate] >= '{_wiql_time(since)}' "
            f"{until_clause}"
            "ORDER BY [System.ChangedDate] DESC"
        )
        # timePrecision: compare date-times, not just dates.
        response = self._request(
            "POST", self._project_url(project, "wit/wiql"), params={"timePrecision": "true"}, json={"query": wiql}
        )
        ids = [item["id"] for item in response.json().get("workItems", [])]

        items: list[dict[str, Any]] = []
        for start in range(0, len(ids), WORK_ITEM_BATCH_SIZE):
            batch = ids[start:start + WORK_ITEM_BATCH_SIZE]
            response = self._request(
                "POST",
                self._project_url(project, "wit/workitemsbatch"),
                json={"ids": batch, "fields": WORK_ITEM_FIELDS, "errorPolicy": "Omit"},
            )
            for raw in response.json().get("value", []):
                if raw:
                    items.append(self.normalize_work_item(project, raw))
        return items

    def normalize_work_item(self, project: str, raw: dict[str, Any]) -> dict[str, Any]:
        fields = raw.get("fields", {})
        assigned_name, assigned_email = _identity(fields.get("System.AssignedTo"))
        created_name, created_email = _identity(fields.get("System.CreatedBy"))
        points = fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
        if points is None:
            points = fields.get("Microsoft.VSTS.Scheduling.Effort")
        work_item_id = int(raw.get("id") or fields.get("System.Id"))
        return {
            "id": work_item_id,
            "project": fields.get("System.TeamProject") or project,
            "work_item_type": fields.get("System.WorkItemType") or "",
            "title": fields.get("System.Title") or "",
            "state": fields.get("System.State"),
            "assigned_to": assigned_name,
            "assigned_to_email": assigned_email,
            "created_by": created_name,
            "created_by_email": created_email,
            "created_date": _date(fields.get("System.CreatedDate")),
            "activated_date": _date(fields.get("Microsoft.VSTS.Common.ActivatedDate")),
            "resolved_date": _date(fields.get("Microsoft.VSTS.Common.ResolvedDate")),
            "closed_date": _date(fields.get("Microsoft.VSTS.Common.ClosedDate")),
            "changed_date": _date(fields.get("System.ChangedDate")),
            "story_points": float(points) if isinstance(points, (int, float)) else None,
            "iteration_path": fields.get("System.IterationPath"),
            "area_path": fields.get("System.AreaPath"),
            "tags": split_tags(fields.get("System.Tags")),
            "url": f"{self.base_url}/{quote(project, safe='')}/_workitems/edit/{work_item_id}",
        }

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    def fetch_pipeline_runs(self, project: str, since: datetime, until: datetime | None = None) -> list[dict[str, Any]]:
        """Completed pipeline runs that finished in ``[since, until)`` (normalized dicts)."""
        runs: list[dict[str, Any]] = []
        continuation: str | None = None
        while True:
            params: dict[str, Any] = {
                "minTime": _wiql_time(since),
                "queryOrder": "finishTimeDescending",
                "statusFilter": "completed",
                "$top": BUILD_PAGE_SIZE,
            }
            if until:
                params["maxTime"] = _wiql_time(until)
            if continuation:
                params["continuationToken"] = continuation
            response = self._request("GET", self._project_url(project, "build/builds"), params=params)
            runs.extend(self.normalize_run(project, raw) for raw in response.json().get("value", []))
            continuation = response.headers.get("x-ms-continuationtoken")
            if not continuation:
                return runs

    def normalize_run(self, project: str, raw: dict[str, Any]) -> dict[str, Any]:
        definition = raw.get("definition") or {}
        repository = raw.get("repository") or {}
        requested_name, requested_email = _identity(raw.get("requestedFor"))
        return {
            "project": project,
            "build_id": int(raw["id"]),
            "build_number": raw.get("buildNumber"),
            "definition_id": definition.get("id"),
            "definition_name": definition.get("name") or "",
            "status": raw.get("status"),
            "result": raw.get("result"),
            "reason": raw.get("reason"),
            "source_branch": raw.get("sourceBranch"),
            "source_version": raw.get("sourceVersion"),
            "repository_name": repository.get("name"),
            "repository_type": repository.get("type"),
            "requested_for": requested_name,
            "requested_for_email": requested_email,
            "queue_time": _date(raw.get("queueTime")),
            "start_time": _date(raw.get("startTime")),
            "finish_time": _date(raw.get("finishTime")),
            "url": ((raw.get("_links") or {}).get("web") or {}).get("href"),
        }
