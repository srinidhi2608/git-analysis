import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.ingest import run_ingestion
from app.models import Base, Commit, Developer, PullRequest, PullRequestComment, Repository


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class RunIngestionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _pr_payload(self, title: str) -> dict:
        created = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        return {
            "repository": "org/repo",
            "pr_number": 5,
            "title": title,
            "author": "alice",
            "created_at": _iso(created),
            "merged_at": _iso(created + timedelta(hours=2)),
            "review_comments_count": 1,
            "commit_count": 1,
            "commits": [{"commit_hash": "abc", "message": "init", "committed_at": _iso(created)}],
            "comments": [
                {
                    "external_id": "cmt-1",
                    "comment_type": "pr_comment",
                    "author_login": "bob",
                    "body": "Nice",
                    "created_at": _iso(created + timedelta(hours=1)),
                }
            ],
        }

    def test_repeated_ingestion_updates_rows_without_duplicates(self):
        with patch("app.ingest.GitHubIngestionService") as service:
            service.return_value.fetch_all.return_value = [self._pr_payload("First title")]
            self.assertEqual(run_ingestion(self.session), 1)
            service.return_value.fetch_all.return_value = [self._pr_payload("Updated title")]
            self.assertEqual(run_ingestion(self.session), 1)

        self.assertEqual(self.session.query(PullRequest).count(), 1)
        self.assertEqual(self.session.query(PullRequest).one().title, "Updated title")
        self.assertEqual(self.session.query(PullRequest).one().cycle_time_minutes, 120)
        self.assertEqual(self.session.query(Commit).count(), 1)
        self.assertEqual(self.session.query(PullRequestComment).count(), 1)
        self.assertEqual(self.session.query(Developer).count(), 1)

    def test_database_rejects_duplicate_pull_requests(self):
        repo = Repository(name="org/repo", is_active=True)
        dev = Developer(github_username="alice")
        self.session.add_all([repo, dev])
        self.session.flush()
        created = datetime(2026, 3, 1)
        for _ in range(2):
            self.session.add(
                PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=1, title="t", created_at=created)
            )
        with self.assertRaises(IntegrityError):
            self.session.flush()


if __name__ == "__main__":
    unittest.main()
