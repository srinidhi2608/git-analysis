import os
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from app.dora_metrics import get_developer_dora_metrics, get_team_dora_metrics
from app.models import Base, Developer, PullRequest, Repository


class DoraMetricsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_team_and_developer_dora_metrics_are_derived_from_pr_data(self):
        alice = Developer(github_username="alice", team_name="Core")
        bob = Developer(github_username="bob", team_name="Core")
        repo = Repository(name="org/repo", is_active=True)
        self.session.add_all([alice, bob, repo])
        self.session.flush()

        now = datetime.now()
        self.session.add_all(
            [
                PullRequest(
                    repo_id=repo.id,
                    developer_id=alice.id,
                    pr_number=1,
                    title="Improve API",
                    created_at=now - timedelta(days=10),
                    merged_at=now - timedelta(days=9, hours=12),
                    cycle_time_minutes=12 * 60,
                    review_comments_count=2,
                    review_count=1,
                    approvals_count=1,
                    requested_changes_count=0,
                    first_review_comment_at=now - timedelta(days=9, hours=20),
                    last_review_comment_at=now - timedelta(days=9, hours=18),
                ),
                PullRequest(
                    repo_id=repo.id,
                    developer_id=bob.id,
                    pr_number=2,
                    title="Refactor auth",
                    created_at=now - timedelta(days=7),
                    merged_at=now - timedelta(days=5),
                    cycle_time_minutes=48 * 60,
                    review_comments_count=3,
                    review_count=1,
                    approvals_count=0,
                    requested_changes_count=1,
                    first_review_comment_at=now - timedelta(days=6, hours=18),
                    last_review_comment_at=now - timedelta(days=5, hours=12),
                ),
                PullRequest(
                    repo_id=repo.id,
                    developer_id=alice.id,
                    pr_number=3,
                    title="Open PR",
                    created_at=now - timedelta(days=2),
                    merged_at=None,
                    cycle_time_minutes=None,
                    review_comments_count=0,
                    review_count=0,
                    approvals_count=0,
                    requested_changes_count=0,
                ),
            ]
        )
        self.session.commit()

        team_metrics = get_team_dora_metrics(self.session)
        developer_metrics = get_developer_dora_metrics(self.session, "alice")

        self.assertEqual(team_metrics["summary"]["pull_request_count"], 3)
        self.assertEqual(team_metrics["summary"]["merged_pull_request_count"], 2)
        self.assertAlmostEqual(team_metrics["summary"]["change_failure_proxy_rate"], 50.0)
        self.assertAlmostEqual(team_metrics["summary"]["approval_rate"], 50.0)
        self.assertGreater(team_metrics["summary"]["merge_frequency_per_week"], 0)
        self.assertTrue(len(team_metrics["weekly_trends"]) >= 1)

        self.assertIsNotNone(developer_metrics)
        self.assertEqual(developer_metrics["github_username"], "alice")
        self.assertEqual(developer_metrics["summary"]["pull_request_count"], 2)
        self.assertEqual(developer_metrics["summary"]["merged_pull_request_count"], 1)
        self.assertAlmostEqual(developer_metrics["summary"]["approval_rate"], 100.0)
        self.assertAlmostEqual(developer_metrics["summary"]["change_failure_proxy_rate"], 0.0)

    def test_developer_dora_metrics_returns_none_for_unknown_user(self):
        self.assertIsNone(get_developer_dora_metrics(self.session, "missing"))


if __name__ == "__main__":
    unittest.main()
