"""GitHub GraphQL ingestion service.

Fetches closed Pull Requests from the GitHub GraphQL API for each active
repository listed in config.yaml, covering the last 7 days.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.config import settings
from app.config_loader import load_active_repositories

logger = logging.getLogger(__name__)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# GraphQL query: paginate PR shells first to avoid GitHub's nested-node limit.
PR_LIST_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      states: [CLOSED, MERGED, OPEN]
      orderBy: { field: UPDATED_AT, direction: DESC }
      first: 100
      after: $after
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        mergedAt
        closedAt
      }
    }
  }
}
"""

PR_DETAIL_SELECTION = """
number
title
createdAt
mergedAt
closedAt
additions
deletions
changedFiles
author { login }
reviews(first: 100) {
  totalCount
  nodes {
    id
    author { login }
    state
    body
    submittedAt
    comments(first: 100) {
      totalCount
      nodes {
        id
        body
        createdAt
        path
        author { login }
      }
    }
  }
}
comments(first: 100) {
  totalCount
  nodes {
    id
    body
    createdAt
    author { login }
  }
}
commits(first: 100) {
  totalCount
  nodes {
    commit {
      oid
      message
      committedDate
    }
  }
}
reviewDecision
"""

DETAIL_BATCH_SIZE = 10


class RateLimitError(Exception):
    """Raised when the GitHub API rate limit is exceeded."""


class GitHubIngestionService:
    """Fetches closed/merged Pull Request data from the GitHub GraphQL API.

    Parameters
    ----------
    config_path:
        Path to the YAML file that lists active repositories.
    lookback_days:
        How many days back to fetch PRs for (default 7).
    """

    def __init__(self, config_path: str = "config.yaml", lookback_days: int = 7) -> None:
        self.config_path = config_path
        self.lookback_days = lookback_days
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": "Bearer " + settings.github_token,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all(self) -> list[dict[str, Any]]:
        """Fetch PRs for all active repositories in the config file.

        Returns
        -------
        list[dict]
            One dict per pull request, across all repositories.
        """
        repos = load_active_repositories(self.config_path)
        results: list[dict[str, Any]] = []
        for repo_entry in repos:
            repo_name: str = repo_entry["name"]
            try:
                owner, name = repo_name.split("/", 1)
            except ValueError:
                logger.warning("Skipping invalid repository name: %s", repo_name)
                continue
            logger.info("Fetching PRs for %s", repo_name)
            prs = self._fetch_repo_prs(owner, name)
            results.extend(prs)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_repo_prs(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Fetch and filter PRs for a single repository."""
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        prs: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            data = self._run_query(PR_LIST_QUERY, {"owner": owner, "repo": repo, "after": cursor})
            repository_data = data["data"]["repository"]
            if repository_data is None:
                logger.warning("Repository %s/%s not found on GitHub; skipping.", owner, repo)
                break
            pr_connection = repository_data["pullRequests"]
            nodes: list[dict] = pr_connection["nodes"]
            page_info: dict = pr_connection["pageInfo"]

            recent_pr_numbers: list[int] = []
            for node in nodes:
                # Use mergedAt if available, otherwise closedAt, to determine recency.
                relevant_date_str: str | None = node.get("mergedAt") or node.get("closedAt")
                if not relevant_date_str:
                    continue
                relevant_date = datetime.fromisoformat(relevant_date_str.replace("Z", "+00:00"))
                # Filter client-side: ordering by UPDATED_AT means we cannot stop
                # pagination early based on mergedAt/closedAt alone.
                if relevant_date >= since:
                    recent_pr_numbers.append(node["number"])

            for pr_number_batch in self._chunked(recent_pr_numbers, DETAIL_BATCH_SIZE):
                for detailed_node in self._fetch_pr_details_batch(owner, repo, pr_number_batch):
                    prs.append(self._normalize_pr(owner, repo, detailed_node))

            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]

        return prs

    def _fetch_pr_details_batch(
        self,
        owner: str,
        repo: str,
        pr_numbers: list[int],
    ) -> list[dict[str, Any]]:
        if not pr_numbers:
            return []

        query = self._build_pr_details_batch_query(pr_numbers)
        variables: dict[str, Any] = {"owner": owner, "repo": repo}
        for index, pr_number in enumerate(pr_numbers):
            variables[f"number{index}"] = pr_number

        data = self._run_query(query, variables)
        repository_data = data["data"]["repository"]
        if repository_data is None:
            logger.warning("Repository %s/%s not found while loading PR details.", owner, repo)
            return []

        detailed_prs: list[dict[str, Any]] = []
        for index, pr_number in enumerate(pr_numbers):
            pull_request = repository_data.get(f"pr_{index}")
            if pull_request is None:
                logger.warning("PR #%s not found in %s/%s.", pr_number, owner, repo)
                continue
            detailed_prs.append(pull_request)
        return detailed_prs

    def _build_pr_details_batch_query(self, pr_numbers: list[int]) -> str:
        variable_definitions = ["$owner: String!", "$repo: String!"]
        aliased_pull_requests: list[str] = []
        for index, _ in enumerate(pr_numbers):
            variable_name = f"number{index}"
            variable_definitions.append(f"${variable_name}: Int!")
            aliased_pull_requests.append(
                f"""
                pr_{index}: pullRequest(number: ${variable_name}) {{
                  {PR_DETAIL_SELECTION}
                }}
                """
            )

        return f"""
query({", ".join(variable_definitions)}) {{
  repository(owner: $owner, name: $repo) {{
    {"".join(aliased_pull_requests)}
  }}
}}
"""

    def _chunked(self, items: list[int], size: int) -> list[list[int]]:
        return [items[index:index + size] for index in range(0, len(items), size)]

    def _normalize_pr(self, owner: str, repo: str, node: dict) -> dict[str, Any]:
        """Convert a raw GraphQL PR node into a clean dict."""
        review_nodes: list[dict] = node["reviews"]["nodes"]
        pr_comment_nodes: list[dict] = node["comments"]["nodes"]
        commit_nodes: list[dict] = node["commits"]["nodes"]

        # Unique reviewers (excluding the PR author themselves).
        author_login: str | None = (node.get("author") or {}).get("login")
        reviewer_logins: list[str] = list(
            {
                (r.get("author") or {}).get("login", "")
                for r in review_nodes
                if (r.get("author") or {}).get("login") != author_login
            }
            - {""}
        )

        approvals_count = sum(1 for review in review_nodes if review.get("state") == "APPROVED")
        requested_changes_count = sum(
            1 for review in review_nodes if review.get("state") == "CHANGES_REQUESTED"
        )

        comments: list[dict[str, Any]] = []
        for review in review_nodes:
            review_author = (review.get("author") or {}).get("login")
            review_state = review.get("state")
            review_body = (review.get("body") or "").strip()
            submitted_at = review.get("submittedAt")
            review_id = review.get("id")
            if review_body and review_id:
                comments.append(
                    {
                        "external_id": f"{review_id}:review",
                        "comment_type": "review",
                        "author_login": review_author,
                        "body": review_body,
                        "created_at": submitted_at,
                        "path": None,
                        "review_state": review_state,
                    }
                )

            for comment in review["comments"]["nodes"]:
                comments.append(
                    {
                        "external_id": comment.get("id"),
                        "comment_type": "review_comment",
                        "author_login": (comment.get("author") or {}).get("login"),
                        "body": comment.get("body") or "",
                        "created_at": comment.get("createdAt"),
                        "path": comment.get("path"),
                        "review_state": review_state,
                    }
                )

        for comment in pr_comment_nodes:
            comments.append(
                {
                    "external_id": comment.get("id"),
                    "comment_type": "pr_comment",
                    "author_login": (comment.get("author") or {}).get("login"),
                    "body": comment.get("body") or "",
                    "created_at": comment.get("createdAt"),
                    "path": None,
                    "review_state": None,
                }
            )

        feedback_timestamps = sorted(
            comment["created_at"]
            for comment in comments
            if comment.get("created_at") and comment.get("author_login") != author_login
        )

        # Total review comments = sum of per-review inline comment counts plus
        # top-level PR issue comments. Review bodies are stored separately for
        # qualitative analysis but are not double-counted here.
        review_comment_count: int = (
            sum(r["comments"]["totalCount"] for r in review_nodes)
            + node["comments"]["totalCount"]
        )

        return {
            "repository": f"{owner}/{repo}",
            "pr_number": node["number"],
            "title": node["title"],
            "author": author_login,
            "reviewers": reviewer_logins,
            "created_at": node["createdAt"],
            "merged_at": node.get("mergedAt"),
            "review_comments_count": review_comment_count,
            "commit_count": node["commits"]["totalCount"],
            "changed_files": node.get("changedFiles") or 0,
            "additions": node.get("additions") or 0,
            "deletions": node.get("deletions") or 0,
            "review_count": node["reviews"]["totalCount"],
            "reviewers_count": len(reviewer_logins),
            "approvals_count": approvals_count,
            "requested_changes_count": requested_changes_count,
            "review_decision": node.get("reviewDecision"),
            "first_review_comment_at": feedback_timestamps[0] if feedback_timestamps else None,
            "last_review_comment_at": feedback_timestamps[-1] if feedback_timestamps else None,
            "comments": [comment for comment in comments if comment.get("external_id")],
            "commits": [
                {
                    "commit_hash": commit["commit"]["oid"],
                    "message": commit["commit"]["message"],
                    "committed_at": commit["commit"].get("committedDate"),
                }
                for commit in commit_nodes
                if commit.get("commit") and commit["commit"].get("oid")
            ],
        }

    def _run_query(self, query: str, variables: dict) -> dict[str, Any]:
        """Execute a GraphQL query, handling rate-limit responses.

        Raises
        ------
        RateLimitError
            If the secondary rate limit is hit and the retry is exhausted.
        requests.HTTPError
            For non-rate-limit HTTP errors.
        """
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            response = self._session.post(
                GITHUB_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=30,
            )

            if response.status_code == 200:
                payload = response.json()
                # GraphQL errors surface inside the JSON body.
                if "errors" in payload:
                    errors = payload["errors"]
                    if self._is_rate_limit_error(errors):
                        wait = 60
                        logger.warning(
                            "GraphQL rate limit error (attempt %d/%d). Waiting %d seconds.",
                            attempt,
                            max_retries,
                            wait,
                        )
                        if attempt == max_retries:
                            raise RateLimitError(f"GraphQL rate limit: {errors}")
                        time.sleep(wait)
                        continue
                    raise RuntimeError(f"GitHub GraphQL errors: {errors}")
                return payload

            if response.status_code == 403:
                # Primary rate limit: Retry-After header tells us when to resume.
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(
                    "Rate limit hit (403). Waiting %d seconds (attempt %d/%d).",
                    retry_after,
                    attempt,
                    max_retries,
                )
                if attempt == max_retries:
                    raise RateLimitError(
                        f"GitHub rate limit exceeded after {max_retries} retries."
                    )
                time.sleep(retry_after)
                continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(
                    "Secondary rate limit hit (429). Waiting %d seconds (attempt %d/%d).",
                    retry_after,
                    attempt,
                    max_retries,
                )
                if attempt == max_retries:
                    raise RateLimitError(
                        f"GitHub secondary rate limit exceeded after {max_retries} retries."
                    )
                time.sleep(retry_after)
                continue

            response.raise_for_status()

        # Should not be reached.
        raise RateLimitError("Exhausted retries without a successful response.")

    def _is_rate_limit_error(self, errors: list[dict]) -> bool:
        """Return True if any GraphQL error is a rate-limit error."""
        for error in errors:
            error_type = error.get("type", "")
            message = error.get("message", "")
            if error_type == "RATE_LIMITED" or "rate limit" in message.lower():
                return True
        return False
