"""Code changes of the GitHub pull requests linked to a story (Story Analyzer deep analysis).

Uses the GitHub REST API (``GET /repos/{owner}/{repo}/pulls/{number}/files``)
with GITHUB_TOKEN. Lock files, minified/generated files and binaries are
skipped; test files are marked so the LLM can tell whether tests were added.
The diffs are cut to a budget before they are sent to the LLM.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)

MAX_PULL_REQUESTS = 5
MAX_FILES_PER_PR = 300
# Characters of diff sent to the LLM in total, and per file.
CODE_BUDGET_CHARS = 40_000
FILE_PATCH_CHARS = 4_000

SKIPPED_FILES = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock)$"
    r"|\.min\.(js|css)$|\.map$|(^|/)(dist|build|vendor|node_modules|__snapshots__)/"
    r"|\.(png|jpe?g|gif|ico|svg|pdf|zip|jar|woff2?|ttf|eot|mp4|lock)$",
    re.IGNORECASE,
)
TEST_FILES = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress|playwright)/|(_test|\.test|\.spec|Test|Tests|_spec)\.[a-z]+$|(^|/)test_[^/]+$", re.IGNORECASE)


def _api_url() -> str:
    return (settings.github_api_url or "https://api.github.com").rstrip("/")


def fetch_pr_changes(repository: str, number: int, session: requests.Session | None = None) -> dict[str, Any]:
    """Changed files of one PR: ``{repository, number, files, skipped_files, error}``."""
    session = session or requests.Session()
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    result: dict[str, Any] = {"repository": repository, "number": number, "files": files, "skipped_files": skipped, "error": None}
    page = 1
    try:
        while len(files) + len(skipped) < MAX_FILES_PER_PR:
            response = session.get(
                f"{_api_url()}/repos/{repository}/pulls/{number}/files",
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            if response.status_code in (401, 403):
                result["error"] = f"GitHub refused access ({response.status_code}): check GITHUB_TOKEN can read {repository}"
                return result
            if response.status_code == 404:
                result["error"] = "pull request not found (deleted, or the token cannot see the repository)"
                return result
            response.raise_for_status()
            batch = response.json()
            for item in batch:
                name = item.get("filename") or ""
                if SKIPPED_FILES.search(name) or not item.get("patch"):
                    skipped.append(name)
                    continue
                files.append(
                    {
                        "filename": name,
                        "status": item.get("status"),
                        "additions": item.get("additions", 0),
                        "deletions": item.get("deletions", 0),
                        "is_test": bool(TEST_FILES.search(name)),
                        "patch": item["patch"],
                    }
                )
            if len(batch) < 100:
                break
            page += 1
    except requests.RequestException as exc:
        result["error"] = f"could not load the PR from GitHub ({exc})"
    return result


def code_prompt_payload(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PR diffs for the LLM within ``CODE_BUDGET_CHARS`` (non-test files first, then tests)."""
    total_files = sum(len(change["files"]) for change in changes) or 1
    per_file = max(min(FILE_PATCH_CHARS, CODE_BUDGET_CHARS // total_files), 600)
    budget = CODE_BUDGET_CHARS
    payload = []
    for change in changes:
        files = []
        for item in sorted(change["files"], key=lambda f: (f["is_test"], -(f["additions"] + f["deletions"]))):
            patch = item["patch"]
            if budget <= 0:
                files.append({**{k: item[k] for k in ("filename", "status", "additions", "deletions", "is_test")}, "patch": "[not included: diff budget used up]"})
                continue
            clipped = patch if len(patch) <= per_file else patch[:per_file] + "\n…[diff truncated]"
            budget -= len(clipped)
            files.append({**{k: item[k] for k in ("filename", "status", "additions", "deletions", "is_test")}, "patch": clipped})
        payload.append(
            {
                "pull_request": f"{change['repository']}#{change['number']}",
                "title": change.get("title"),
                "status": change.get("status"),
                "files": files,
                "files_not_shown": change["skipped_files"][:50],
                "error": change["error"],
            }
        )
    return payload


def summarize_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the page shows per PR (no diff text)."""
    return [
        {
            "pull_request": f"{change['repository']}#{change['number']}",
            "url": f"https://github.com/{change['repository']}/pull/{change['number']}",
            "title": change.get("title"),
            "status": change.get("status"),
            "files": [{k: f[k] for k in ("filename", "status", "additions", "deletions", "is_test")} for f in change["files"]],
            "skipped_files": len(change["skipped_files"]),
            "error": change["error"],
        }
        for change in changes
    ]
