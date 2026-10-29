import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.developer_analytics import build_developer_review_analytics
from app.dora_metrics import get_developer_dora_metrics, get_team_dora_metrics
from app.github_ingestion import GitHubIngestionService
from app.models import Base, Developer, PullRequest, Repository
from app.queries import (
    get_pr_cycle_time_by_developer,
    get_prs_per_week,
    get_target_branches,
    get_team_performance_metrics,
)
from app.snapshot import load_snapshot, resolve_snapshot, save_snapshot
from app.teams import SkippedAuthors, TeamConfig

NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    repo = Repository(name="org/repo", is_active=True)
    alice = Developer(github_username="alice")
    db.add_all([repo, alice])
    db.flush()
    for number, branch in enumerate(["main", "main", "develop", "release/1.2", None], start=1):
        db.add(
            PullRequest(
                repo_id=repo.id,
                developer_id=alice.id,
                pr_number=number,
                title=f"PR {number}",
                base_branch=branch,
                created_at=NOW - timedelta(days=3),
                merged_at=NOW - timedelta(days=2),
                cycle_time_minutes=60 * number,
            )
        )
    db.commit()


class BranchFilterTests(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        _seed(self.db)

    def test_branch_list_counts_prs_and_unknown(self):
        result = get_target_branches(self.db, days=30)
        self.assertEqual(result["branches"][0], {"name": "main", "pull_requests": 2})
        self.assertEqual({b["name"] for b in result["branches"]}, {"main", "develop", "release/1.2"})
        self.assertEqual(result["unknown"], 1)

    def test_stats_only_include_selected_branches(self):
        def merged(**kw):
            return get_team_dora_metrics(self.db, window_days=30, **kw)["summary"]["merged_pull_request_count"]

        self.assertEqual(merged(), 5)
        self.assertEqual(merged(branches=["main"]), 2)
        self.assertEqual(merged(branches=["main", "develop"]), 3)
        self.assertEqual(merged(branches=[]), 5)  # empty selection = all branches
        self.assertEqual(sum(w["mergedPrs"] for w in get_prs_per_week(self.db, days=30, branches=["develop"])), 1)
        self.assertEqual(get_team_performance_metrics(self.db, days=30, branches=["release/1.2"])[0]["total_prs"], 1)
        # main PRs have cycle times of 1h and 2h
        self.assertEqual(get_pr_cycle_time_by_developer(self.db, days=30, branches=["main"])[0]["cycleTimeHours"], 1.5)
        dev = get_developer_dora_metrics(self.db, "alice", window_days=30, branches=["develop"])
        self.assertEqual(dev["summary"]["pull_request_count"], 1)
        analytics = build_developer_review_analytics(self.db, "alice", branches=["main"])
        self.assertEqual(analytics["metrics"]["pull_request_count"], 2)

    def test_base_branch_is_ingested_and_kept_in_snapshots(self):
        service = GitHubIngestionService.__new__(GitHubIngestionService)
        service.team_config = TeamConfig()
        service.skipped = SkippedAuthors()
        node = {
            "number": 9,
            "title": "t",
            "baseRefName": "release/2.0",
            "createdAt": "2026-01-01T00:00:00Z",
            "author": {"login": "alice"},
            "reviews": {"totalCount": 0, "nodes": []},
            "comments": {"totalCount": 0, "nodes": []},
            "commits": {"totalCount": 0, "nodes": []},
        }
        self.assertEqual(service._normalize_pr("org", "repo", node)["base_branch"], "release/2.0")

        directory = tempfile.mkdtemp()
        save_snapshot(self.db, directory)
        target = _session()
        load_snapshot(target, resolve_snapshot(directory))
        self.assertEqual(target.query(PullRequest).filter_by(pr_number=4).one().base_branch, "release/1.2")


if __name__ == "__main__":
    unittest.main()
