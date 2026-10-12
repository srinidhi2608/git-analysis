"""GitHub GraphQL ingestion service.

Fetches Pull Requests (open, closed and merged) from the GitHub GraphQL API
for each active repository listed in config.yaml, limited to PRs that had
activity (were updated) within the lookback window.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.config import settings
from app.config_loader import load_active_repositories
from app.teams import SkippedAuthors, TeamConfig, load_team_config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# GraphQL query: paginate PR shells first to avoid GitHub's nested-node limit.
# Ordered by most recently updated so pagination can stop at the lookback cutoff.
PR_LIST_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      states: [CLOSED, MERGED, OPEN]
      first: 100
      after: $after
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        updatedAt
        author { login }
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
      author { user { login } }
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
    team_config:
        Team membership. When teams are configured, PRs, reviews, comments and
        commits authored by anyone who is not a listed member are skipped (and
        reported via ``skipped``). Loaded from ``config_path`` when omitted.
    """

    def __init__(
        self,
        config_path: str = "config.yaml",
        lookback_days: int = 7,
        team_config: TeamConfig | None = None,
    ) -> None:
        self.config_path = config_path
        self.lookback_days = lookback_days
        self.team_config = team_config if team_config is not None else load_team_config(config_path)
        self.skipped = SkippedAuthors()
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
        self.skipped = SkippedAuthors()
        if self.team_config.enabled:
            logger.info(
                "Team filter active: only ingesting data from members of %s.",
                ", ".join(self.team_config.team_names),
            )
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
            logger.info("Fetched %d PRs for %s", len(prs), repo_name)
            results.extend(prs)
        self.skipped.log_summary()
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
            reached_cutoff = False
            for node in nodes:
                updated_at_str: str | None = node.get("updatedAt")
                if not updated_at_str:
                    continue
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                if updated_at < since:
                    # Results are ordered by updatedAt DESC, so every later PR is older too.
                    reached_cutoff = True
                    break
                author_login = (node.get("author") or {}).get("login")
                if not self.team_config.is_member(author_login):
                    # Not a team member: do not download the PR's details at all.
                    self.skipped.record(author_login, "pull_request", f"{owner}/{repo}")
                    continue
                recent_pr_numbers.append(node["number"])

            for pr_number_batch in self._chunked(recent_pr_numbers, DETAIL_BATCH_SIZE):
                for detailed_node in self._fetch_pr_details_batch(owner, repo, pr_number_batch):
                    prs.append(self._normalize_pr(owner, repo, detailed_node))

            if reached_cutoff or not page_info["hasNextPage"]:
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

    @staticmethod
    def _chunked(items: list[int], size: int) -> list[list[int]]:
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

        repository = f"{owner}/{repo}"
        member_comments: list[dict[str, Any]] = []
        for comment in comments:
            if self.team_config.is_member(comment.get("author_login")):
                member_comments.append(comment)
            else:
                kind = "review" if comment["comment_type"] == "review" else "comment"
                self.skipped.record(comment.get("author_login"), kind, repository)

        member_commits: list[dict[str, Any]] = []
        for commit_node in commit_nodes:
            commit = commit_node.get("commit") or {}
            if not commit.get("oid"):
                continue
            # Commits whose author email is not linked to a GitHub account have no
            # login; they are attributed to the PR author.
            commit_login = ((commit.get("author") or {}).get("user") or {}).get("login")
            if commit_login and not self.team_config.is_member(commit_login):
                self.skipped.record(commit_login, "commit", repository)
                continue
            member_commits.append(
                {
                    "commit_hash": commit["oid"],
                    "message": commit.get("message") or "",
                    "committed_at": commit.get("committedDate"),
                }
            )

        # PR-level review signals (counts, first/last feedback time) describe the
        # member's PR and are kept as reported by GitHub; only the non-member
        # authored comment text and commits are dropped above.
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
            "comments": [comment for comment in member_comments if comment.get("external_id")],
            "commits": member_commits,
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

            if response.status_code == 403 and self._is_rate_limited_response(response):
                # Primary rate limit: wait until the reset time (or Retry-After).
                retry_after = self._retry_after_seconds(response)
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
                retry_after = self._retry_after_seconds(response)
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

            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"GitHub rejected the request ({response.status_code}). Check that GITHUB_TOKEN is "
                    f"valid and has read access to the configured repositories: {response.text[:200]}"
                )
            response.raise_for_status()

        # Should not be reached.
        raise RateLimitError("Exhausted retries without a successful response.")

    @staticmethod
    def _is_rate_limited_response(response: requests.Response) -> bool:
        """Distinguish a rate-limit 403 from a permissions/auth 403."""
        if response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers:
            return True
        return "rate limit" in response.text.lower()

    @staticmethod
    def _retry_after_seconds(response: requests.Response, default: int = 60, maximum: int = 900) -> int:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(int(retry_after), maximum)
        reset_at = response.headers.get("X-RateLimit-Reset")
        if reset_at and reset_at.isdigit():
            return min(max(int(reset_at) - int(time.time()), 1), maximum)
        return default

    def _is_rate_limit_error(self, errors: list[dict]) -> bool:
        """Return True if any GraphQL error is a rate-limit error."""
        for error in errors:
            error_type = error.get("type", "")
            message = error.get("message", "")
            if error_type == "RATE_LIMITED" or "rate limit" in message.lower():
                return True
        return False
