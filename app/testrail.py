"""TestRail test cases linked to a story (Story Analyzer deep analysis).

TestRail's Azure DevOps integration links a test case to a work item through
the case's **References** field (e.g. ``1234`` or ``AB#1234``). Cases are found
with ``get_cases`` filtered by that reference (TestRail 6.5.2+), and from
TestRail case links or ``C<id>`` mentions on the story itself.

For each case: title, preconditions, steps and expected results, its latest
result in the most recent test runs (status, comment, tester, date) and the
names of attachments on the case and that result (the evidence).

config.yaml::

    testrail:
      url: "https://yourcompany.testrail.io"
      user: "qa-bot@yourcompany.com"
      project_ids: [1]
      suite_ids: []                # needed for projects with several suites
      reference_format: "{id}"     # how the story id appears in References, e.g. "AB#{id}"
      runs_to_scan: 10             # recent runs searched for the latest results

The API key goes in .env as TESTRAIL_API_KEY.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from app.config import settings

logger = logging.getLogger(__name__)

MAX_CASES = 30
CASE_LINK = re.compile(r"cases/view/(\d+)", re.IGNORECASE)
CASE_MENTION = re.compile(r"(?<![\w/])C(\d{2,9})\b")
DEFAULT_STATUSES = {1: "Passed", 2: "Blocked", 3: "Untested", 4: "Retest", 5: "Failed"}


class TestRailError(RuntimeError):
    __test__ = False  # not a pytest test class


@dataclass
class TestRailConfig:
    __test__ = False
    url: str = ""
    user: str = ""
    project_ids: list[int] = field(default_factory=list)
    suite_ids: list[int] = field(default_factory=list)
    reference_format: str = "{id}"
    runs_to_scan: int = 10
    api_key: str = field(default="", repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.user and self.api_key and self.project_ids)

    def missing(self) -> list[str]:
        missing = []
        if not self.url:
            missing.append("testrail.url in config.yaml")
        if not self.user:
            missing.append("testrail.user in config.yaml")
        if not self.project_ids:
            missing.append("testrail.project_ids in config.yaml")
        if not self.api_key:
            missing.append("TESTRAIL_API_KEY in .env")
        return missing

    def reference(self, work_item_id: int) -> str:
        return self.reference_format.replace("{id}", str(work_item_id))


def _ints(value: object) -> list[int]:
    if isinstance(value, (int, str)):
        value = [value]
    return [int(item) for item in value or [] if str(item).strip().isdigit()] if isinstance(value, list) else []


def parse_testrail_config(data: dict | None, api_key: str | None = None) -> TestRailConfig:
    raw = (data or {}).get("testrail") or {}
    if not isinstance(raw, dict):
        raw = {}
    return TestRailConfig(
        url=str(raw.get("url") or "").strip().rstrip("/"),
        user=str(raw.get("user") or "").strip(),
        project_ids=_ints(raw.get("project_ids")),
        suite_ids=_ints(raw.get("suite_ids")),
        reference_format=str(raw.get("reference_format") or "{id}"),
        runs_to_scan=max(0, int(raw.get("runs_to_scan", 10) or 0)),
        api_key=(api_key if api_key is not None else settings.testrail_api_key) or "",
    )


def load_testrail_config(config_path: str = "config.yaml") -> TestRailConfig:
    path = Path(config_path)
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    return parse_testrail_config(data)


def case_ids_in(texts: list[str]) -> set[int]:
    """TestRail case ids in links (…/cases/view/123) and mentions (C123)."""
    ids: set[int] = set()
    for text in texts:
        ids |= {int(match) for match in CASE_LINK.findall(text or "")}
        ids |= {int(match) for match in CASE_MENTION.findall(text or "")}
    return ids


class TestRailClient:
    __test__ = False

    def __init__(self, config: TestRailConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.auth = (config.user, config.api_key)
        self.session.headers.update({"Content-Type": "application/json"})
        self.request_count = 0

    def get(self, endpoint: str) -> Any:
        """``GET index.php?/api/v2/<endpoint>`` (endpoint may contain ``&filters``)."""
        url = f"{self.config.url}/index.php?/api/v2/{endpoint}"
        for attempt in range(2):
            self.request_count += 1
            response = self.session.get(url, timeout=30)
            if response.status_code == 429 and attempt == 0:
                time.sleep(min(int(response.headers.get("Retry-After", "5") or 5), 30))
                continue
            if response.status_code == 401:
                raise TestRailError("TestRail rejected the credentials (testrail.user / TESTRAIL_API_KEY).")
            if response.status_code == 403:
                raise TestRailError(f"TestRail denied access to {endpoint.split('&')[0]} (check the user's project permissions).")
            if response.status_code >= 400:
                try:
                    detail = response.json().get("error")
                except ValueError:
                    detail = response.text[:200]
                raise TestRailError(f"TestRail {endpoint.split('&')[0]} failed ({response.status_code}): {detail}")
            return response.json()
        raise TestRailError("TestRail kept rate limiting the requests.")

    def _paged(self, endpoint: str, key: str, limit: int = 250) -> list[dict[str, Any]]:
        """TestRail 6.7+ wraps lists in ``{key: [...], _links: {next}}``; older versions return a list."""
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self.get(f"{endpoint}&limit={limit}&offset={offset}")
            if isinstance(data, list):
                return items + data
            batch = data.get(key) or []
            items.extend(batch)
            if not (data.get("_links") or {}).get("next") or not batch:
                return items
            offset += len(batch)

    def statuses(self) -> dict[int, str]:
        try:
            return {int(s["id"]): s.get("label") or s.get("name") or str(s["id"]) for s in self.get("get_statuses")}
        except TestRailError:
            return dict(DEFAULT_STATUSES)

    def cases_by_reference(self, reference: str) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        suites = self.config.suite_ids or [None]
        for project_id in self.config.project_ids:
            for suite_id in suites:
                endpoint = f"get_cases/{project_id}&refs={requests.utils.quote(reference)}"
                if suite_id:
                    endpoint += f"&suite_id={suite_id}"
                cases.extend(self._paged(endpoint, "cases"))
        # refs is a comma-separated list: keep exact matches only (the filter may also match substrings).
        wanted = reference.strip().lower()
        return [case for case in cases if wanted in [ref.strip().lower() for ref in str(case.get("refs") or "").split(",")]]

    def attachments(self, kind: str, item_id: int) -> list[str]:
        try:
            data = self.get(f"get_attachments_for_{kind}/{item_id}")
        except TestRailError:
            return []
        items = data.get("attachments", []) if isinstance(data, dict) else data
        return [item.get("name") or item.get("filename") or str(item.get("id")) for item in items or []]


def _case_text(case: dict[str, Any]) -> dict[str, Any]:
    """Preconditions, steps and expected results across TestRail templates."""
    steps: list[dict[str, str]] = []
    for step in case.get("custom_steps_separated") or []:
        if isinstance(step, dict):
            steps.append({"step": str(step.get("content") or ""), "expected": str(step.get("expected") or "")})
    extra = {
        key.removeprefix("custom_"): value
        for key, value in case.items()
        if key.startswith("custom_") and isinstance(value, str) and value.strip() and key != "custom_steps_separated"
    }
    return {
        "preconditions": extra.pop("preconds", ""),
        "steps": steps or ([{"step": extra.pop("steps", ""), "expected": extra.pop("expected", "")}] if extra.get("steps") else []),
        "other_fields": extra,
    }


def gather_test_cases(config: TestRailConfig, work_item_id: int, texts: list[str], client: TestRailClient | None = None) -> dict[str, Any]:
    """Cases linked to the story, with their latest results and evidence. Never raises."""
    if not config.configured:
        return {"configured": False, "missing": config.missing(), "cases": [], "errors": []}
    client = client or TestRailClient(config)
    errors: list[str] = []
    found: dict[int, dict[str, Any]] = {}
    try:
        for case in client.cases_by_reference(config.reference(work_item_id)):
            found[int(case["id"])] = {**case, "_linked_by": "References"}
    except TestRailError as exc:
        errors.append(str(exc))
    for case_id in sorted(case_ids_in(texts) - set(found)):
        try:
            found[case_id] = {**client.get(f"get_case/{case_id}"), "_linked_by": "link on the story"}
        except TestRailError as exc:
            errors.append(f"C{case_id}: {exc}")
    cases = list(found.values())[:MAX_CASES]

    # Latest result per case: newest run first that contains the case.
    latest: dict[int, dict[str, Any]] = {}
    statuses = client.statuses() if cases else {}
    if cases and config.runs_to_scan:
        wanted = {int(case["id"]) for case in cases}
        try:
            runs = []
            for project_id in config.project_ids:
                data = client.get(f"get_runs/{project_id}&limit={config.runs_to_scan}")
                runs.extend(data.get("runs", []) if isinstance(data, dict) else data)
            runs.sort(key=lambda run: run.get("created_on") or 0, reverse=True)
            for run in runs[: config.runs_to_scan]:
                for test in client._paged(f"get_tests/{run['id']}", "tests"):
                    case_id = int(test.get("case_id") or 0)
                    if case_id in wanted and case_id not in latest:
                        latest[case_id] = {"test_id": int(test["id"]), "run": run.get("name"), "run_id": run.get("id"),
                                           "status": statuses.get(int(test.get("status_id") or 0), str(test.get("status_id")))}
                if len(latest) == len(wanted):
                    break
            for case_id, entry in latest.items():
                results = client.get(f"get_results/{entry['test_id']}&limit=1")
                results = results.get("results", []) if isinstance(results, dict) else results
                if results:
                    entry.update(comment=(results[0].get("comment") or "")[:1500], tested_on=results[0].get("created_on"),
                                 defects=results[0].get("defects"))
                entry["attachments"] = client.attachments("test", entry["test_id"])
        except TestRailError as exc:
            errors.append(f"latest results: {exc}")

    output = []
    for case in cases:
        case_id = int(case["id"])
        output.append(
            {
                "id": case_id,
                "title": case.get("title"),
                "url": f"{config.url}/index.php?/cases/view/{case_id}",
                "refs": case.get("refs"),
                "linked_by": case["_linked_by"],
                **_case_text(case),
                "attachments": client.attachments("case", case_id),
                "latest": latest.get(case_id),
            }
        )
    return {"configured": True, "missing": [], "cases": output, "errors": errors, "requests": client.request_count}
