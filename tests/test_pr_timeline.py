import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.azure_devops import AzureDevOpsClient
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_history, run_devops_ingestion, upsert_pipeline_run, upsert_work_item
from app.devops_metrics import compute_dora
from app.lifecycle_config import parse_lifecycle_config
from app.main import app
from app.models import AzurePipelineRun, Base, Commit, Developer, PullRequest, Repository
from app.pr_timeline import build_pr_rows, pr_timeline_csv, summarize_prs
from app.teams import parse_team_config
from app.time_window import TimeWindow
from app.work_item_links import LinkIndex

from tests.test_github_ingestion import _service

LIFECYCLE = parse_lifecycle_config({})
TEAMS = parse_team_config({"teams": {"Payments": [{"name": "Alice Anderson", "github": "alice", "azure_devops": "alice@corp.com"}]}})
DEVOPS = DevOpsConfig(organization="corp", projects=["Shop"], deployment_pattern="(?i)deploy", production_pattern="(?i)prod", pat="x")
T0 = datetime(2026, 10, 1, 9, 0)
NOW = datetime(2026, 10, 30, 12, 0)
WINDOW = TimeWindow(datetime(2026, 10, 1), datetime(2026, 11, 1))


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _work_item(db, item_id, item_type="User Story", parent=None):
    item = upsert_work_item(db, {"id": item_id, "project": "Shop", "work_item_type": item_type, "title": f"Item {item_id}",
                                 "state": "Active", "created_date": T0, "changed_date": T0, "tags": [], "parent_id": parent})
    replace_history(db, item, [{"rev": 1, "changed_at": T0, "state": "Active"}], [])
    return item


def _pr(db, number, h, *, title="Change", state="MERGED", reviewers=1, lines=(50, 10), refs=None, commit_msg="work"):
    """``h``: hours after T0 of (first commit, created, first review, approved, merged); None = missing."""
    repo = db.query(Repository).filter_by(name="org/shop").one_or_none() or Repository(name="org/shop", is_active=True)
    dev = db.query(Developer).filter_by(github_username="alice").one_or_none() or Developer(github_username="alice", team_name="Payments")
    db.add_all([repo, dev])
    db.flush()
    at = lambda hours: T0 + timedelta(hours=hours) if hours is not None else None  # noqa: E731
    pr = PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=number, title=title, created_at=at(h[1]),
                     first_reviewed_at=at(h[2]), approved_at=at(h[3]), merged_at=at(h[4]), state=state,
                     reviewers_count=reviewers, additions=lines[0], deletions=lines[1], merge_commit_sha=f"merge{number}",
                     work_item_refs=refs, closed_at=at(h[4]) if state == "CLOSED" else None)
    db.add(pr)
    db.flush()
    if h[0] is not None:
        db.add(Commit(pr_id=pr.id, developer_id=dev.id, commit_hash=f"head{number}", message=commit_msg, committed_at=at(h[0])))
    return pr


def _run(db, build_id, name, finish, *, sha=None, result="succeeded", **details):
    run = upsert_pipeline_run(db, {"project": "Shop", "build_id": build_id, "definition_id": 1, "definition_name": name,
                                   "status": "completed", "result": result, "source_version": sha,
                                   "repository_name": "shop", "finish_time": finish})
    for key, value in details.items():
        setattr(run, key, value)
    return run


class PrTimelineTests(unittest.TestCase):
    def setUp(self):
        self.db = db = _session()
        _work_item(db, 100)                       # story
        _work_item(db, 101, "Task", parent=100)   # child task of the story
        # 1: full flow, linked to the story through its child task; deployed by sha.
        _pr(db, 1, (0, 4, 10, 20, 22), title="AB#101 card form")
        _run(db, 9, "Deploy Prod", T0 + timedelta(hours=30), sha="merge1")
        # 2: no story, unreviewed merge, large; 3: open + stale, approved (waiting to merge), refers to an unsynced item.
        _pr(db, 2, (None, 0, None, None, 5), reviewers=0, lines=(900, 100))
        _pr(db, 3, (1, 2, 3, 6, None), state="OPEN", refs="555")
        # 4: abandoned, linked via a commit message.
        _pr(db, 4, (0, 1, None, None, None), state="CLOSED", commit_msg="AB#100 try")
        # 5: merged after the last production deployment -> not deployed.
        _pr(db, 5, (30, 31, 32, 33, 40), title="AB#100 late")
        db.commit()

    def rows(self):
        rows, configured = build_pr_rows(self.db, WINDOW, TEAMS, LinkIndex(self.db, LIFECYCLE), DEVOPS, now=NOW)
        return {row["number"]: row for row in rows}, configured

    def test_phases_and_links(self):
        rows, configured = self.rows()
        self.assertTrue(configured)
        full = rows[1]
        self.assertEqual([(p["phase"], p["hours"]) for p in full["timeline"]],
                         [("coding", 4), ("pickup", 6), ("review", 10), ("merge", 2), ("deploy", 8)])
        self.assertEqual((full["cycle_hours"], full["lead_to_production_hours"]), (22, 30))
        self.assertEqual(full["stories"], [100])
        self.assertEqual(full["work_items"][0]["sources"], ["AB# in PR"])
        self.assertEqual(full["author"], "Alice Anderson")
        self.assertEqual(full["flags"], [])
        self.assertEqual(rows[4]["stories"], [100])  # AB# in commit message

    def test_flags(self):
        rows, _ = self.rows()
        # Merged before the repository's next production deployment, so shipped by it.
        self.assertEqual(sorted(rows[2]["flags"]), ["large_pr", "no_story", "unreviewed_merge"])
        self.assertEqual(rows[2]["deployed_at"], "2026-10-02T15:00:00Z")
        self.assertEqual(rows[5]["flags"], ["not_deployed"])
        self.assertTrue(rows[5]["timeline"][-1]["ongoing"])
        self.assertEqual([p["phase"] for p in rows[2]["timeline"]], ["pickup", "deploy"])
        self.assertEqual(sorted(rows[3]["flags"]), ["stale_open", "story_not_synced"])
        self.assertEqual(rows[3]["unresolved_work_item_refs"], [555])
        current = rows[3]["timeline"][-1]
        self.assertEqual((current["phase"], current["ongoing"], current["end"]), ("merge", True, None))
        self.assertIn("abandoned", rows[4]["flags"])

    def test_summary_csv_and_api(self):
        rows, configured = self.rows()
        summary = summarize_prs(list(rows.values()), configured)
        self.assertEqual((summary["pull_requests"], summary["merged"], summary["open"], summary["abandoned"]), (5, 3, 1, 1))
        self.assertEqual((summary["without_story"], summary["without_story_pct"]), (1, 20.0))
        text = pr_timeline_csv(list(rows.values()))
        self.assertTrue(text.startswith("﻿repository,pr_number,title"))
        self.assertIn("no_story; unreviewed_merge", text)

        app.dependency_overrides[get_db] = lambda: self.db
        try:
            client = TestClient(app)
            data = client.get("/api/pull-requests?date_from=2026-10-01&date_to=2026-10-31").json()
            self.assertEqual(data["summary"]["pull_requests"], 5)
            csv_response = client.get("/api/reports/pull-requests.csv?date_from=2026-10-01&date_to=2026-10-31")
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn("pull-requests-20261001-20261101.csv", csv_response.headers["content-disposition"])
        finally:
            app.dependency_overrides.clear()


class RunStoryTests(unittest.TestCase):
    def test_runs_resolve_stories_from_azure_commits_and_prs(self):
        db = _session()
        _work_item(db, 100)
        _work_item(db, 101, "Task", parent=100)
        _work_item(db, 200)
        _work_item(db, 300, "Bug")
        _pr(db, 1, (0, 1, 2, 3, 4), title="AB#200 api")
        run = _run(db, 7, "Deploy Prod", T0 + timedelta(hours=10), sha="unrelated",
                   build_work_item_ids="101", change_shas="head1,abc", change_work_item_refs="300")
        db.commit()
        links = {link.id: link for link in LinkIndex(db, LIFECYCLE).for_run(run)}
        self.assertEqual(links[101].sources, {"Azure (build)"})
        self.assertEqual(links[101].story_id, 100)  # child task -> story
        self.assertEqual(links[300].sources, {"AB# in commit"})
        self.assertIsNone(links[300].story_id)  # a bug without a parent story
        self.assertEqual(links[200].sources, {"via PR org/shop#1"})

        dora = compute_dora(db, DEVOPS, TEAMS, WINDOW.start.date(), (WINDOW.end - timedelta(days=1)).date())
        listed = dora["runs"][0]
        self.assertEqual(listed["stories"], [100, 200])
        self.assertEqual(sorted(item["id"] for item in listed["work_items"]), [101, 200, 300])

    def test_client_and_sync_fetch_run_details_once(self):
        session = MagicMock()
        session.headers = {}

        def respond(method, url, **kwargs):
            response = MagicMock(status_code=200, ok=True, headers={"Content-Type": "application/json"})
            if url.endswith("/workitems"):
                response.json.return_value = {"value": [{"id": "12", "url": "x"}]}
            else:
                response.json.return_value = {"value": [
                    {"id": "a" * 40, "message": "Fix AB#34 and ab#35", "type": "GitHub"},
                    {"id": "C123", "message": "AB#99 tfvc changeset", "type": "TfsVersionControl"},
                ]}
            return response

        session.request.side_effect = respond
        details = AzureDevOpsClient(DEVOPS, session=session).fetch_run_details("Shop", 5)
        self.assertEqual(details, {"work_item_ids": [12], "commit_shas": ["a" * 40], "work_item_refs": [34, 35]})

        db = _session()
        client = MagicMock(request_count=0)
        client.fetch_work_items.return_value = []
        client.fetch_pipeline_runs.return_value = [{"project": "Shop", "build_id": 5, "definition_name": "CI", "finish_time": T0}]
        client.fetch_run_details.return_value = details
        first = run_devops_ingestion(db, devops_config=DEVOPS, client=client, config_path="/nonexistent.yaml")
        second = run_devops_ingestion(db, devops_config=DEVOPS, client=client, config_path="/nonexistent.yaml")
        self.assertEqual((first.run_details_fetched, second.run_details_fetched), (1, 0))
        run = db.query(AzurePipelineRun).one()
        self.assertEqual((run.build_work_item_ids, run.change_work_item_refs), ("12", "34,35"))


class ReviewTimesTests(unittest.TestCase):
    def test_first_review_and_approval_come_from_reviews_by_others(self):
        node = {
            "number": 1, "title": "t", "createdAt": "2026-10-01T09:00:00Z", "author": {"login": "alice"},
            "reviews": {"totalCount": 3, "nodes": [
                {"id": "r0", "author": {"login": "alice"}, "state": "COMMENTED", "body": "", "submittedAt": "2026-10-01T09:30:00Z", "comments": {"totalCount": 0, "nodes": []}},
                {"id": "r1", "author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": "", "submittedAt": "2026-10-01T11:00:00Z", "comments": {"totalCount": 0, "nodes": []}},
                {"id": "r2", "author": {"login": "bob"}, "state": "APPROVED", "body": "", "submittedAt": "2026-10-01T15:00:00Z", "comments": {"totalCount": 0, "nodes": []}},
            ]},
            "comments": {"totalCount": 0, "nodes": []}, "commits": {"totalCount": 0, "nodes": []},
        }
        pr = _service()._normalize_pr("org", "repo", node)
        self.assertEqual((pr["first_reviewed_at"], pr["approved_at"]), ("2026-10-01T11:00:00Z", "2026-10-01T15:00:00Z"))


if __name__ == "__main__":
    unittest.main()
