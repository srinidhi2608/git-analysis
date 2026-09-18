import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
