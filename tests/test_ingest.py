import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.dora_metrics import get_team_dora_metrics
from app.ingest import run_ingestion, sync_developer_teams
from app.models import Base, Commit, Developer, PullRequest, PullRequestComment, Repository
from app.queries import get_all_developers, get_prs_per_week, get_team_performance_metrics
from app.teams import parse_team_config


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

    def _write_config(self, text: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _pr_payload(self, title: str, author: str = "alice", pr_number: int = 5) -> dict:
        created = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        return {
            "repository": "org/repo",
            "pr_number": pr_number,
            "title": title,
            "author": author,
            "created_at": _iso(created),
            "merged_at": _iso(created + timedelta(hours=2)),
            "review_comments_count": 1,
            "commit_count": 1,
            "commits": [{"commit_hash": f"abc{pr_number}", "message": "init", "committed_at": _iso(created)}],
            "comments": [
                {
                    "external_id": f"cmt-{pr_number}",
                    "comment_type": "pr_comment",
                    "author_login": "bob",
                    "body": "Nice",
                    "created_at": _iso(created + timedelta(hours=1)),
                }
            ],
        }

    def test_repeated_ingestion_updates_rows_without_duplicates(self):
        config_path = self._write_config("repositories: []\n")  # no teams: everyone is ingested
        with patch("app.ingest.GitHubIngestionService") as service:
            service.return_value.fetch_all.return_value = [self._pr_payload("First title")]
            self.assertEqual(run_ingestion(self.session, config_path=config_path).upserted, 1)
            service.return_value.fetch_all.return_value = [self._pr_payload("Updated title")]
            self.assertEqual(run_ingestion(self.session, config_path=config_path).upserted, 1)

        self.assertEqual(self.session.query(PullRequest).count(), 1)
        self.assertEqual(self.session.query(PullRequest).one().title, "Updated title")
        self.assertEqual(self.session.query(PullRequest).one().cycle_time_minutes, 120)
        self.assertEqual(self.session.query(Commit).count(), 1)
        self.assertEqual(self.session.query(PullRequestComment).count(), 1)
        self.assertEqual(self.session.query(Developer).count(), 1)

    def test_ingestion_passes_stored_updated_at_and_reports_stats(self):
        config_path = self._write_config("repositories: []\n")
        payload = self._pr_payload("PR")
        payload["updated_at"] = "2026-03-02T10:00:00Z"
        with patch("app.ingest.GitHubIngestionService") as service:
            service.return_value.fetch_all.return_value = [payload]
            service.return_value.unchanged_count = 0
            service.return_value.request_count = 3
            first = run_ingestion(self.session, config_path=config_path)
            service.return_value.fetch_all.return_value = []
            service.return_value.unchanged_count = 1
            service.return_value.request_count = 1
            second = run_ingestion(self.session, config_path=config_path)
            known = service.call_args.kwargs["known_updated_at"]
            run_ingestion(self.session, config_path=config_path, full_refresh=True)
            full_refresh_known = service.call_args.kwargs["known_updated_at"]

        self.assertEqual((first.upserted, first.github_requests), (1, 3))
        self.assertEqual((second.upserted, second.unchanged, second.github_requests), (0, 1, 1))
        self.assertEqual(known, {("org/repo", 5): datetime(2026, 3, 2, 10, 0)})
        self.assertIsNone(full_refresh_known)

    def test_ingestion_assigns_teams_and_skips_non_members(self):
        config_path = self._write_config(
            'repositories: []\nteams:\n  GrowthTeam:\n    - name: "Alice"\n  IppPayFac:\n    - name: "dave"\n'
        )
        with patch("app.ingest.GitHubIngestionService") as service:
            service.return_value.fetch_all.return_value = [
                self._pr_payload("Member PR", author="alice", pr_number=1),
                self._pr_payload("Outsider PR", author="mallory", pr_number=2),
            ]
            self.assertEqual(run_ingestion(self.session, config_path=config_path).upserted, 1)

        self.assertEqual(service.call_args.kwargs["team_config"].team_names, ["GrowthTeam", "IppPayFac"])
        self.assertEqual(self.session.query(PullRequest).count(), 1)
        alice = self.session.query(Developer).filter_by(github_username="alice").one()
        self.assertEqual(alice.team_name, "GrowthTeam")
        self.assertIsNone(self.session.query(Developer).filter_by(github_username="mallory").one_or_none())

    def test_team_filters_restrict_dashboard_queries(self):
        repo = Repository(name="org/repo", is_active=True)
        self.session.add(repo)
        self.session.flush()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for login, number in (("alice", 1), ("dave", 2), ("old-member", 3)):
            dev = Developer(github_username=login)
            self.session.add(dev)
            self.session.flush()
            self.session.add(
                PullRequest(
                    repo_id=repo.id,
                    developer_id=dev.id,
                    pr_number=number,
                    title=f"PR {number}",
                    created_at=now - timedelta(days=2),
                    merged_at=now - timedelta(days=1),
                    cycle_time_minutes=1440,
                )
            )
        self.session.commit()

        sync_developer_teams(
            self.session,
            parse_team_config({"teams": {"GrowthTeam": ["alice"], "IppPayFac": ["dave"]}}),
        )

        all_teams = ["GrowthTeam", "IppPayFac"]
        self.assertEqual(
            [d["github_username"] for d in get_all_developers(self.session, teams=all_teams)], ["alice", "dave"]
        )
        self.assertEqual(
            [d["github_username"] for d in get_all_developers(self.session, teams=["IppPayFac"])], ["dave"]
        )
        growth = get_team_dora_metrics(self.session, teams=["GrowthTeam"])
        self.assertEqual(growth["summary"]["pull_request_count"], 1)
        self.assertEqual(get_team_dora_metrics(self.session, teams=all_teams)["summary"]["pull_request_count"], 2)
        self.assertEqual(sum(w["mergedPrs"] for w in get_prs_per_week(self.session, teams=["IppPayFac"])), 1)
        performance = get_team_performance_metrics(self.session, teams=all_teams)
        self.assertEqual({row["team_name"]: row["total_prs"] for row in performance}, {"GrowthTeam": 1, "IppPayFac": 1})

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
