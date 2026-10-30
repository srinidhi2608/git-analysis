import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dora_metrics import get_team_dora_metrics
from app.models import Base, Commit, Developer, PullRequest, PullRequestComment, Repository
from app.queries import get_prs_per_week
from app.time_window import TimeWindow
from app.snapshot import list_snapshots, load_snapshot, resolve_snapshot, save_snapshot

NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db) -> None:
    repo = Repository(name="org/repo", is_active=True)
    alice = Developer(github_username="alice", team_name="Core")
    db.add_all([repo, alice])
    db.flush()

    def pr(number: int, created_days: float, merged_days: float | None, reviewers: int) -> PullRequest:
        item = PullRequest(
            repo_id=repo.id,
            developer_id=alice.id,
            pr_number=number,
            title=f"PR {number}",
            created_at=NOW - timedelta(days=created_days),
            merged_at=None if merged_days is None else NOW - timedelta(days=merged_days),
            cycle_time_minutes=None if merged_days is None else (created_days - merged_days) * 1440,
            reviewers_count=reviewers,
            review_count=reviewers,
            github_updated_at=NOW - timedelta(days=1),
        )
        db.add(item)
        return item

    pr(1, created_days=2, merged_days=1, reviewers=1)  # merged in a 7-day window, reviewed
    pr(2, created_days=10, merged_days=3, reviewers=0)  # created before, merged inside 7 days: ghost merge
    pr(3, created_days=20, merged_days=15, reviewers=0)  # merged outside 7 days, inside 30: ghost merge
    first = pr(4, created_days=1, merged_days=None, reviewers=0)  # open
    db.flush()
    db.add(Commit(pr_id=first.id, developer_id=alice.id, commit_hash="abc", message="m", committed_at=NOW))
    db.add(
        PullRequestComment(
            pr_id=first.id, external_id="c1", comment_type="pr_comment", author_login="bob", body="hi", created_at=NOW
        )
    )
    db.commit()


class LookbackWindowTests(unittest.TestCase):
    def test_metrics_follow_the_window_and_count_ghost_merges(self):
        db = _session()
        _seed(db)

        week = get_team_dora_metrics(db, window_days=7)["summary"]
        month = get_team_dora_metrics(db, window_days=30)["summary"]

        self.assertEqual(week["window_days"], 7)
        self.assertEqual(week["merged_pull_request_count"], 2)  # PRs 1 and 2
        self.assertEqual(week["ghost_merge_count"], 1)  # PR 2
        self.assertEqual(week["ghost_merge_rate"], 50.0)
        self.assertEqual(month["merged_pull_request_count"], 3)
        self.assertEqual(month["ghost_merge_count"], 2)
        self.assertEqual(sum(w["mergedPrs"] for w in get_prs_per_week(db, days=7)), 2)
        self.assertEqual(sum(w["mergedPrs"] for w in get_prs_per_week(db, days=30)), 3)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def test_snapshot_round_trip_restores_into_an_empty_database(self):
        source = _session()
        _seed(source)
        saved = save_snapshot(source, self.directory)
        self.assertEqual(saved["counts"]["pull_requests"], 4)
        self.assertEqual([s["name"] for s in list_snapshots(self.directory)], [saved["name"]])

        target = _session()
        load_snapshot(target, resolve_snapshot(self.directory))

        self.assertEqual(target.query(PullRequest).count(), 4)
        self.assertEqual(target.query(Commit).count(), 1)
        self.assertEqual(target.query(PullRequestComment).count(), 1)
        restored = target.query(PullRequest).filter_by(pr_number=2).one()
        original = source.query(PullRequest).filter_by(pr_number=2).one()
        self.assertEqual(restored.merged_at, original.merged_at)
        self.assertEqual(restored.github_updated_at, original.github_updated_at)
        self.assertEqual(restored.developer.github_username, "alice")
        window = TimeWindow.last_days(30)
        self.assertEqual(
            get_team_dora_metrics(target, window=window)["summary"],
            get_team_dora_metrics(source, window=window)["summary"],
        )

    def test_loading_twice_upserts_without_duplicates(self):
        db = _session()
        _seed(db)
        save_snapshot(db, self.directory)
        load_snapshot(db, resolve_snapshot(self.directory))
        load_snapshot(db, resolve_snapshot(self.directory))
        self.assertEqual(db.query(PullRequest).count(), 4)
        self.assertEqual(db.query(Commit).count(), 1)
        self.assertEqual(db.query(Developer).count(), 1)

    def test_only_the_newest_snapshots_are_kept_and_names_are_validated(self):
        db = _session()
        for index in range(3):
            path = os.path.join(self.directory, f"snapshot-2026010{index}T000000Z.json.gz")
            open(path, "wb").close()
        save_snapshot(db, self.directory, keep=2)
        self.assertEqual(len(list_snapshots(self.directory)), 2)
        with self.assertRaises(ValueError):
            resolve_snapshot(self.directory, "../etc/passwd")
        self.assertIsNone(resolve_snapshot(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
