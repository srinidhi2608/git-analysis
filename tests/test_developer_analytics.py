import asyncio
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
# Point Ollama at a closed port so tests never depend on a local LLM.
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:9")

from app.ai_analyzers import DeveloperAnalysisResult
from app.developer_analytics import get_developer_review_analytics
from app.models import Base, Commit, Developer, PullRequest, PullRequestComment, Repository


class DeveloperAnalyticsTests(unittest.TestCase):
    def setUp(self):
        # StaticPool shares one in-memory connection across threads: analytics
        # queries run in a worker thread via run_in_threadpool.
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_builds_grounded_developer_analytics(self):
        developer = Developer(github_username="alice", team_name="Core")
        reviewer = Developer(github_username="bob", team_name="Core")
        repository = Repository(name="org/repo", is_active=True)
        self.session.add_all([developer, reviewer, repository])
        self.session.flush()

        created_at = datetime(2026, 1, 10, 10, 0, 0)
        merged_at = created_at + timedelta(hours=30)
        first_pr = PullRequest(
            repo_id=repository.id,
            developer_id=developer.id,
            pr_number=101,
            title="Improve auth validation",
            created_at=created_at,
            merged_at=merged_at,
            cycle_time_minutes=1800,
            review_comments_count=3,
            commit_count=3,
            changed_files=4,
            additions=120,
            deletions=30,
            review_count=2,
            reviewers_count=1,
            approvals_count=1,
            requested_changes_count=1,
            review_decision="APPROVED",
            first_review_comment_at=created_at + timedelta(hours=5),
            last_review_comment_at=created_at + timedelta(hours=8),
        )
        second_pr = PullRequest(
            repo_id=repository.id,
            developer_id=developer.id,
            pr_number=102,
            title="Refactor dashboard loading states",
            created_at=created_at + timedelta(days=2),
            merged_at=created_at + timedelta(days=2, hours=10),
            cycle_time_minutes=600,
            review_comments_count=1,
            commit_count=1,
            changed_files=2,
            additions=40,
            deletions=10,
            review_count=1,
            reviewers_count=1,
            approvals_count=1,
            requested_changes_count=0,
            review_decision="APPROVED",
        )
        self.session.add_all([first_pr, second_pr])
        self.session.flush()

        self.session.add_all(
            [
                Commit(
                    pr_id=first_pr.id,
                    developer_id=developer.id,
                    commit_hash="c1",
                    message="initial",
                    committed_at=created_at + timedelta(hours=1),
                ),
                Commit(
                    pr_id=first_pr.id,
                    developer_id=developer.id,
                    commit_hash="c2",
                    message="address review",
                    committed_at=created_at + timedelta(hours=6),
                ),
                Commit(
                    pr_id=first_pr.id,
                    developer_id=developer.id,
                    commit_hash="c3",
                    message="more fixes",
                    committed_at=created_at + timedelta(hours=9),
                ),
                PullRequestComment(
                    pr_id=first_pr.id,
                    external_id="review-1",
                    comment_type="review_comment",
                    author_login="bob",
                    body="Please add tests for this auth edge case and clean up naming.",
                    path="app/auth.py",
                    review_state="CHANGES_REQUESTED",
                    created_at=created_at + timedelta(hours=5),
                ),
                PullRequestComment(
                    pr_id=first_pr.id,
                    external_id="review-2",
                    comment_type="pr_comment",
                    author_login="bob",
                    body="Looks good after the fix.",
                    path=None,
                    review_state=None,
                    created_at=created_at + timedelta(hours=8),
                ),
            ]
        )
        self.session.commit()

        analytics = asyncio.run(get_developer_review_analytics(self.session, "alice"))

        self.assertIsNotNone(analytics)
        self.assertEqual(analytics["github_username"], "alice")
        self.assertEqual(analytics["metrics"]["pull_request_count"], 2)
        self.assertGreater(analytics["metrics"]["average_rework_commits_per_pr"], 0)
        self.assertGreater(analytics["metrics"]["average_time_to_first_followup_hours"], 0)
        categories = {item["category"] for item in analytics["breakdown"]["comment_categories"]}
        self.assertIn("testing", categories)
        self.assertIn("style", categories)
        self.assertEqual(analytics["summary"]["provider"], "heuristic-fallback")
        self.assertIn("Based on 2 saved PRs", analytics["summary"]["overview"])

    def test_handles_sparse_saved_comment_data(self):
        developer = Developer(github_username="charlie", team_name=None)
        repository = Repository(name="org/repo", is_active=True)
        self.session.add_all([developer, repository])
        self.session.flush()
        self.session.add(
            PullRequest(
                repo_id=repository.id,
                developer_id=developer.id,
                pr_number=201,
                title="Small cleanup",
                created_at=datetime(2026, 1, 12, 9, 0, 0),
                merged_at=datetime(2026, 1, 12, 12, 0, 0),
                cycle_time_minutes=180,
                review_comments_count=0,
            )
        )
        self.session.commit()

        analytics = asyncio.run(get_developer_review_analytics(self.session, "charlie"))

        self.assertEqual(analytics["sample"]["comment_text_items"], 0)
        self.assertEqual(analytics["summary"]["confidence"], "low")
        self.assertTrue(
            any("little or no saved review text" in item for item in analytics["summary"]["risks"])
        )

    def test_ai_analyzer_receives_review_comment_text(self):
        developer = Developer(github_username="dana", team_name=None)
        repository = Repository(name="org/repo", is_active=True)
        self.session.add_all([developer, repository])
        self.session.flush()
        created_at = datetime(2026, 2, 1, 9, 0, 0)
        pull_request = PullRequest(
            repo_id=repository.id,
            developer_id=developer.id,
            pr_number=7,
            title="Add caching layer",
            created_at=created_at,
            merged_at=created_at + timedelta(hours=4),
            cycle_time_minutes=240,
            review_comments_count=1,
            commit_count=1,
        )
        self.session.add(pull_request)
        self.session.flush()
        self.session.add(
            PullRequestComment(
                pr_id=pull_request.id,
                external_id="c-1",
                comment_type="review_comment",
                author_login="erin",
                body="This duplicates the logic in cache.py; extract a helper.",
                path="app/cache.py",
                created_at=created_at + timedelta(hours=1),
            )
        )
        self.session.commit()

        fake_result = DeveloperAnalysisResult(
            coding_standards_score=8,
            design_patterns_summary="Uses a cache-aside pattern.",
            dry_vs_wet_observations="One duplicated block flagged.",
            reviewer_rigor_score=6,
            actionable_feedback=["Extract the shared cache helper."],
        )
        with patch(
            "app.developer_analytics.get_developer_analyzer",
            new=AsyncMock(return_value=fake_result),
        ) as analyzer:
            analytics = asyncio.run(get_developer_review_analytics(self.session, "dana"))

        review_comments = analyzer.await_args.kwargs["review_comments"]
        self.assertEqual(len(review_comments), 1)
        self.assertEqual(review_comments[0]["body"], "This duplicates the logic in cache.py; extract a helper.")
        self.assertEqual(review_comments[0]["path"], "app/cache.py")
        self.assertNotIn("_review_comment_samples", analytics)
        self.assertEqual(analytics["summary"]["design_patterns_summary"], "Uses a cache-aside pattern.")
        self.assertEqual(analytics["summary"]["improvement_areas"], ["Extract the shared cache helper."])


if __name__ == "__main__":
    unittest.main()
