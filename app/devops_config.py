"""Azure DevOps settings from config.yaml (`azure_devops` section) and .env.

Example config.yaml::

    azure_devops:
      organization: "https://dev.azure.com/your-org"   # or just "your-org"
      projects: ["Payments"]
      work_item_types: ["User Story", "Bug", "Task"]
      # Pipelines whose name matches are deployments (DevLake "deploymentPattern").
      deployment_pattern: "(?i)deploy|release"
      # Deployments whose pipeline name matches go to production (DevLake
      # "productionPattern"); leave empty to treat every deployment as production.
      production_pattern: "(?i)prod"
      # Optional: work items that represent production incidents. When set,
      # change failure rate and recovery time are measured from incidents
      # (as in DevLake); otherwise from failed deployment runs.
      incidents:
        work_item_types: ["Bug"]
        tags: ["Incident"]

The personal access token is a secret and goes in .env as AZURE_DEVOPS_PAT.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_WORK_ITEM_TYPES = ["User Story", "Product Backlog Item", "Bug", "Task"]
DEFAULT_DEPLOYMENT_PATTERN = r"(?i)deploy|release"


def _compile(pattern: str | None, name: str) -> re.Pattern | None:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning("Ignoring invalid azure_devops.%s regex %r: %s", name, pattern, exc)
        return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass
class DevOpsConfig:
    organization: str = ""
    projects: list[str] = field(default_factory=list)
    work_item_types: list[str] = field(default_factory=lambda: list(DEFAULT_WORK_ITEM_TYPES))
    deployment_pattern: str = DEFAULT_DEPLOYMENT_PATTERN
    production_pattern: str = ""
    incident_work_item_types: list[str] = field(default_factory=list)
    incident_tags: list[str] = field(default_factory=list)
    pat: str = field(default="", repr=False)

    @property
    def organization_url(self) -> str:
        org = self.organization.strip().rstrip("/")
        if not org:
            return ""
        if org.startswith("http://") or org.startswith("https://"):
            return org
        return f"https://dev.azure.com/{org}"

    @property
    def configured(self) -> bool:
        return bool(self.organization_url and self.projects and self.pat)

    @property
    def incidents_enabled(self) -> bool:
        return bool(self.incident_work_item_types or self.incident_tags)

    def is_deployment(self, pipeline_name: str | None) -> bool:
        pattern = _compile(self.deployment_pattern, "deployment_pattern")
        return bool(pipeline_name and pattern and pattern.search(pipeline_name))

    def is_production(self, pipeline_name: str | None) -> bool:
        pattern = _compile(self.production_pattern, "production_pattern")
        return pattern is None or bool(pipeline_name and pattern.search(pipeline_name))

    def is_incident(self, work_item_type: str | None, tags: list[str]) -> bool:
        if not self.incidents_enabled:
            return False
        if self.incident_work_item_types and (work_item_type or "") not in self.incident_work_item_types:
            return False
        if self.incident_tags:
            wanted = {tag.lower() for tag in self.incident_tags}
            return any(tag.lower() in wanted for tag in tags)
        return True

    def missing(self) -> list[str]:
        """Human-readable list of settings still needed to connect."""
        missing = []
        if not self.organization_url:
            missing.append("azure_devops.organization in config.yaml")
        if not self.projects:
            missing.append("azure_devops.projects in config.yaml")
        if not self.pat:
            missing.append("AZURE_DEVOPS_PAT in .env")
        return missing


def parse_devops_config(data: dict | None, pat: str | None = None) -> DevOpsConfig:
    raw = (data or {}).get("azure_devops") or {}
    if not isinstance(raw, dict):
        logger.warning("Ignoring 'azure_devops' in config.yaml: expected a mapping.")
        raw = {}
    incidents = raw.get("incidents") or {}
    if not isinstance(incidents, dict):
        incidents = {}
    config = DevOpsConfig(
        organization=str(raw.get("organization") or settings.azure_devops_org or "").strip(),
        projects=_string_list(raw.get("projects")),
        deployment_pattern=str(raw.get("deployment_pattern") or DEFAULT_DEPLOYMENT_PATTERN),
        production_pattern=str(raw.get("production_pattern") or ""),
        incident_work_item_types=_string_list(incidents.get("work_item_types")),
        incident_tags=_string_list(incidents.get("tags")),
        pat=(pat if pat is not None else settings.azure_devops_pat) or "",
    )
    types = _string_list(raw.get("work_item_types"))
    if types:
        config.work_item_types = types
    return config


def load_devops_config(config_path: str = "config.yaml") -> DevOpsConfig:
    path = Path(config_path)
    data = None
    if path.exists():
        with path.open("r") as f:
            data = yaml.safe_load(f)
    return parse_devops_config(data)
