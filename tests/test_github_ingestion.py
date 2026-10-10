import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from app.github_ingestion import GitHubIngestionService


class GitHubIngestionNormalizationTests(unittest.TestCase):
    def test_normalize_pr_captures_saved_comments_and_review_metadata(self):
        service = GitHubIngestionService.__new__(GitHubIngestionService)
        node = {
            "number": 42,
            "title": "Improve metrics",
            "createdAt": "2026-01-10T10:00:00Z",
            "mergedAt": "2026-01-11T10:00:00Z",
            "closedAt": "2026-01-11T10:00:00Z",
            "additions": 120,
            "deletions": 20,
            "changedFiles": 5,
            "author": {"login": "alice"},
            "reviews": {
                "totalCount": 1,
                "nodes": [
                    {
                        "id": "review-node-1",
                        "author": {"login": "bob"},
                        "state": "CHANGES_REQUESTED",
                        "body": "Please add tests.",
                        "submittedAt": "2026-01-10T12:00:00Z",
                        "comments": {
                            "totalCount": 1,
                            "nodes": [
                                {
                                    "id": "comment-node-1",
                                    "body": "This needs better naming.",
                                    "createdAt": "2026-01-10T11:00:00Z",
                                    "path": "app/main.py",
                                    "author": {"login": "bob"},
                                }
                            ],
                        },
                    }
                ],
            },
            "comments": {
                "totalCount": 1,
                "nodes": [
                    {
                        "id": "issue-comment-1",
                        "body": "Can you split this PR?",
                        "createdAt": "2026-01-10T13:00:00Z",
                        "author": {"login": "bob"},
                    }
                ],
            },
            "commits": {
                "totalCount": 2,
                "nodes": [
                    {
                        "commit": {
                            "oid": "abc123",
                            "message": "initial",
                            "committedDate": "2026-01-10T10:30:00Z",
                        }
                    }
                ],
            },
            "reviewDecision": "REVIEW_REQUIRED",
        }

        normalized = service._normalize_pr("org", "repo", node)

        self.assertEqual(normalized["repository"], "org/repo")
        self.assertEqual(normalized["review_comments_count"], 2)
        self.assertEqual(normalized["requested_changes_count"], 1)
        self.assertEqual(normalized["reviewers_count"], 1)
        self.assertEqual(normalized["first_review_comment_at"], "2026-01-10T11:00:00Z")
        self.assertEqual(normalized["last_review_comment_at"], "2026-01-10T13:00:00Z")
        self.assertEqual(len(normalized["comments"]), 3)
        self.assertEqual(normalized["comments"][0]["comment_type"], "review")
        self.assertEqual(normalized["commits"][0]["commit_hash"], "abc123")

    def test_fetch_repo_prs_uses_list_then_details_queries_for_recent_prs(self):
        service = GitHubIngestionService.__new__(GitHubIngestionService)
        service.lookback_days = 7
        calls: list[tuple[str, dict]] = []
        now = datetime.now(timezone.utc)

        recent_merged_at = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        stale_merged_at = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")

        detail_node = {
            "number": 42,
            "title": "Recent PR",
            "createdAt": (now - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
            "mergedAt": recent_merged_at,
            "closedAt": recent_merged_at,
            "additions": 10,
            "deletions": 2,
            "changedFiles": 1,
            "author": {"login": "alice"},
            "reviews": {"totalCount": 0, "nodes": []},
            "comments": {"totalCount": 0, "nodes": []},
            "commits": {"totalCount": 1, "nodes": []},
            "reviewDecision": None,
        }

        def fake_run_query(query: str, variables: dict):
            calls.append((query, variables))
            if "pullRequests(" in query:
                return {
                    "data": {
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {"number": 42, "updatedAt": recent_merged_at},
                                    {"number": 43, "updatedAt": stale_merged_at},
                                ],
                            }
                        }
                    }
                }

            return {"data": {"repository": {"pr_0": detail_node}}}

        service._run_query = fake_run_query  # type: ignore[method-assign]

        prs = service._fetch_repo_prs("org", "repo")

        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0]["pr_number"], 42)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["number0"], 42)
        self.assertIn("orderBy: {field: UPDATED_AT, direction: DESC}", calls[0][0])

    def test_fetch_repo_prs_stops_paginating_at_lookback_cutoff_and_includes_open_prs(self):
        service = GitHubIngestionService.__new__(GitHubIngestionService)
        service.lookback_days = 7
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        stale = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        list_calls: list[dict] = []

        def detail_node(number: int) -> dict:
            # An open PR: no mergedAt / closedAt.
            return {
                "number": number,
                "title": f"PR {number}",
                "createdAt": recent,
                "mergedAt": None,
                "closedAt": None,
                "additions": 1,
                "deletions": 0,
                "changedFiles": 1,
                "author": {"login": "alice"},
                "reviews": {"totalCount": 0, "nodes": []},
                "comments": {"totalCount": 0, "nodes": []},
                "commits": {"totalCount": 0, "nodes": []},
                "reviewDecision": None,
            }

        def fake_run_query(query: str, variables: dict):
            if "pullRequests(" in query:
                list_calls.append(variables)
                return {
                    "data": {
                        "repository": {
                            "pullRequests": {
                                # More pages exist, but the stale node means we must stop.
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                "nodes": [
                                    {"number": 9, "updatedAt": recent},
                                    {"number": 8, "updatedAt": stale},
                                ],
                            }
                        }
                    }
                }
            return {"data": {"repository": {"pr_0": detail_node(variables["number0"])}}}

        service._run_query = fake_run_query  # type: ignore[method-assign]

        prs = service._fetch_repo_prs("org", "repo")

        self.assertEqual(len(list_calls), 1)
        self.assertEqual([pr["pr_number"] for pr in prs], [9])
        self.assertIsNone(prs[0]["merged_at"])

    def test_run_query_fails_fast_on_auth_403(self):
        service = GitHubIngestionService.__new__(GitHubIngestionService)
        response = MagicMock(status_code=403, headers={}, text="Resource not accessible by personal access token")
        service._session = MagicMock()
        service._session.post.return_value = response

        with patch("app.github_ingestion.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                service._run_query("query { viewer { login } }", {})

        sleep.assert_not_called()
        self.assertEqual(service._session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
