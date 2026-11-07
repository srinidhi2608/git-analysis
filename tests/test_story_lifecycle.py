import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.azure_devops import normalize_updates
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_history, run_devops_ingestion, upsert_work_item
from app.devops_metrics import StoryFilters
from app.github_ingestion import work_item_refs
from app.lifecycle_config import parse_lifecycle_config
from app.main import app
from app.models import AzureWorkItem, Base, Commit, Developer, PullRequest, Repository
from app.snapshot import load_snapshot, resolve_snapshot, save_snapshot
from app.story_lifecycle import build_story_lifecycles, lifecycle_csv, pull_requests_csv, summarize_lifecycles, transitions_csv
from app.teams import parse_team_config

CONFIG = parse_lifecycle_config({})  # defaults = New / Active / QA Dev ... / Released / Closed
TEAMS = parse_team_config(
    {
        "teams": {
            "Payments": [
                {"name": "Alice Anderson", "github": "alice", "azure_devops": "alice@corp.com", "role": "developer"},
                {"name": "Tom QA", "azure_devops": "tom@corp.com", "role": "tester"},
            ]
        }
    }
)
T0 = datetime(2026, 10, 1, 9, 0)
NOW = datetime(2026, 10, 30, 12, 0)
RANGE = StoryFilters(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _item(db, item_id, steps, *, item_type="User Story", tags=(), points=None, parent=None, relations=(), title="Story"):
    """``steps``: [(hours after T0, state, assignee email)]."""
    final = steps[-1]
    item = upsert_work_item(
        db,
        {
            "id": item_id, "project": "Shop", "work_item_type": item_type, "title": title, "state": final[1],
            "assigned_to": None, "assigned_to_email": final[2], "created_date": T0 + timedelta(hours=steps[0][0]),
            "changed_date": T0 + timedelta(hours=final[0]), "story_points": points, "tags": list(tags), "parent_id": parent,
            "url": f"https://dev.azure.com/corp/Shop/_workitems/edit/{item_id}",
        },
    )
    history, previous = [], None
    for rev, (hours, state, email) in enumerate(steps, 1):
        history.append({"rev": rev, "changed_at": T0 + timedelta(hours=hours), "changed_by": "Tom QA" if email == "tom@corp.com" else "Alice",
                        "changed_by_email": email, "from_state": previous, "state": state, "assigned_to": None, "assigned_to_email": email})
        previous = state
    replace_history(db, item, history, list(relations))
    return item


def _pr(db, number, *, title="Change", branch=None, state="MERGED", merged_hours=None, github_id=None, refs=None, commit_msg=None, lines=(100, 20)):
    repo = db.query(Repository).filter_by(name="org/shop").one_or_none() or Repository(name="org/shop", is_active=True)
    dev = db.query(Developer).filter_by(github_username="alice").one_or_none() or Developer(github_username="alice", team_name="Payments")
    db.add_all([repo, dev])
    db.flush()
    pr = PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=number, title=title, created_at=T0 + timedelta(hours=10),
                     merged_at=T0 + timedelta(hours=merged_hours) if merged_hours else None, state=state, head_branch=branch,
                     github_id=github_id, work_item_refs=refs, additions=lines[0], deletions=lines[1])
    db.add(pr)
    db.flush()
    db.add(Commit(pr_id=pr.id, developer_id=dev.id, commit_hash=f"sha{number}", message=commit_msg or "work", committed_at=T0 + timedelta(hours=8)))
    return pr


A, TOM = "alice@corp.com", "tom@corp.com"
FULL_FLOW = [
    (0, "New", None), (24, "Active", A), (72, "QA Dev In Progress", TOM), (96, "Active", A),  # bounce back to dev
    (120, "QA Dev In Progress", TOM), (144, "QA Dev Complete", TOM), (168, "QAA In Progress", TOM),
    (192, "QAA Complete", TOM), (216, "INT", TOM), (240, "Released", None),
]


class ConfigTests(unittest.TestCase):
    def test_defaults_follow_the_team_workflow_and_release_tags(self):
        self.assertEqual([s.name for s in CONFIG.stages], ["new", "dev", "qa", "qa_done", "qaa", "qaa_done", "int", "released", "closed"])
        self.assertEqual(CONFIG.stage_for("qa dev in progress").name, "qa")
        self.assertEqual(CONFIG.dev_stage.name, "dev")
        self.assertEqual(CONFIG.release_version(["checkout", "Release 2026.10.1"]), "2026.10.1")
        self.assertEqual(CONFIG.release_version(["Release 2026"]), "2026")
        self.assertIsNone(CONFIG.release_version(["Released", "Release-1"]))

    def test_custom_stages_and_bad_entries(self):
        config = parse_lifecycle_config({"lifecycle": {
            "stages": [{"name": "todo", "kind": "wait", "states": ["To Do"]}, {"name": "doing", "states": "Doing"},
                       {"name": "done", "kind": "released", "states": ["Done", "doing"]}, "junk"],
            "story_types": "Product Backlog Item", "release_tag_pattern": "(", "testing": {"enabled": True}}})
        self.assertEqual([(s.name, s.kind, s.states) for s in config.stages],
                         [("todo", "wait", ("To Do",)), ("doing", "work", ("Doing",)), ("done", "released", ("Done",))])
        self.assertEqual(config.story_types, ["Product Backlog Item"])
        self.assertTrue(config.testing_enabled)
        self.assertEqual(config.release_version(["Release 1.2"]), "1.2")  # invalid pattern -> default


class UpdatesParsingTests(unittest.TestCase):
    def test_updates_become_state_history_and_current_links(self):
        updates = [
            {"rev": 1, "revisedBy": {"displayName": "Ann", "uniqueName": "ann@corp.com"},
             "fields": {"System.State": {"newValue": "New"}, "System.ChangedDate": {"newValue": "2026-10-01T09:00:00Z"}},
             "relations": {"added": [{"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://x/_apis/wit/workItems/7", "attributes": {"name": "Parent"}}]}},
            {"rev": 2, "fields": {"System.Title": {"newValue": "renamed"}}},  # ignored: no state/assignee change
            {"rev": 3, "revisedBy": {"displayName": "Ann", "uniqueName": "ann@corp.com"},
             "fields": {"System.State": {"oldValue": "New", "newValue": "Active"}, "System.ChangedDate": {"newValue": "2026-10-02T09:00:00Z"},
                        "System.AssignedTo": {"newValue": {"displayName": "Alice", "uniqueName": "Alice@corp.com"}}},
             "relations": {"added": [{"rel": "ArtifactLink", "url": "vstfs:///GitHub/PullRequest/abc%2F991", "attributes": {"name": "GitHub Pull Request"}}]}},
            {"rev": 4, "fields": {"System.AssignedTo": {"oldValue": {"displayName": "Alice"}}, "System.ChangedDate": {"newValue": "2026-10-03T09:00:00Z"}},
             "relations": {"removed": [{"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://x/_apis/wit/workItems/7"}]}},
        ]
        result = normalize_updates(updates)
        self.assertEqual([(h["rev"], h["from_state"], h["state"], h["assigned_to_email"]) for h in result["history"]],
                         [(1, None, "New", None), (3, "New", "Active", "alice@corp.com"), (4, None, "Active", None)])
        self.assertEqual(result["relations"], [{"rel": "ArtifactLink", "name": "GitHub Pull Request", "url": "vstfs:///GitHub/PullRequest/abc%2F991", "target_id": None}])

    def test_ab_references_from_pr_text(self):
        self.assertEqual(work_item_refs("AB#12 fix", "Fixes ab#7 and AB#12"), "7,12")
        self.assertIsNone(work_item_refs("no refs", None))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.db = db = _session()
        # 101: full flow with a QA bounce, release tag, 4 linked PRs through different sources, a child task and a bug.
        _item(db, 101, FULL_FLOW, tags=["Release 2026.10.1"], points=5,
              relations=[{"rel": "ArtifactLink", "name": "GitHub Pull Request", "url": "vstfs:///GitHub/PullRequest/conn%2F5001", "target_id": None},
                         {"rel": "System.LinkTypes.Related", "name": "Related", "url": "https://x/workItems/301", "target_id": 301}])
        _item(db, 201, [(30, "New", A), (40, "Closed", A)], item_type="Task", parent=101)
        _item(db, 301, [(260, "New", TOM)], item_type="Bug")  # related bug raised after release -> escaped
        _pr(db, 1, title="AB#101 card form", merged_hours=60)
        _pr(db, 2, branch="feature/101-save-card", merged_hours=110)
        _pr(db, 3, github_id=5001, merged_hours=115)
        _pr(db, 4, commit_msg="Wire API AB#201", merged_hours=118)  # via child task
        _pr(db, 5, title="AB#101 follow-up", state="OPEN")
        _pr(db, 6, title="unrelated", branch="release/2026", merged_hours=50)
        # 102: closed directly from New; 103: released without QA/QAA/INT and without a release tag;
        # 104: closed unreleased with merged code; 105: still in progress (in QA).
        _item(db, 102, [(0, "New", A), (5, "Closed", A)])
        _item(db, 103, [(0, "New", A), (10, "Active", A), (50, "Released", A)])
        _item(db, 104, [(0, "New", A), (10, "Active", A), (40, "Closed", A)])
        _pr(db, 7, title="AB#104 spike", merged_hours=30)
        _item(db, 105, [(0, "New", A), (10, "Active", A), (100, "QA Dev In Progress", TOM)])
        db.commit()

    def rows(self, **kw):
        filters = StoryFilters(**{"date_from": RANGE.date_from, "date_to": RANGE.date_to, **kw})
        return {row["id"]: row for row in build_story_lifecycles(self.db, filters, TEAMS, CONFIG, now=NOW)}

    def test_full_flow_story(self):
        row = self.rows()[101]
        self.assertEqual(row["outcome"], "released")
        self.assertEqual(row["release_version"], "2026.10.1")
        self.assertEqual(row["released_at"], "2026-10-11T09:00:00Z")
        self.assertEqual(row["lead_time_hours"], 240)
        self.assertEqual(row["cycle_time_hours"], 216)
        self.assertEqual(row["stage_hours"]["dev"], 72)  # 48 + 24 after the bounce
        self.assertEqual(row["stage_hours"]["qa"], 48)
        # work = dev 72 + qa 48 + qaa 24 + int 24 = 168; wait = qa_done 24 + qaa_done 24 = 48
        self.assertEqual((row["work_hours"], row["wait_hours"], row["flow_efficiency"]), (168, 48, round(168 / 216, 3)))
        self.assertEqual((row["qa_bounces"], row["rework_count"], row["reopened"]), (1, 1, 0))
        self.assertEqual(row["repeated_stages"], ["dev", "qa"])
        self.assertEqual(row["skipped_stages"], [])
        sources = {pr["number"]: pr["sources"] for pr in row["pull_requests"]}
        self.assertEqual(sources, {1: ["AB# in PR"], 2: ["branch name"], 3: ["azure link"], 4: ["via Task #201"], 5: ["AB# in PR"]})
        self.assertEqual((row["prs_merged"], row["prs_open"]), (4, 1))
        self.assertIn("open_pr_at_finish", row["flags"])
        self.assertIsNone(row["code_time_hours"])  # an open PR: code not finished
        self.assertEqual((row["lines_added"], row["lines_per_point"]), (500, 120.0))
        self.assertEqual(row["developers"], [{"key": "alice", "name": "Alice Anderson", "team": "Payments"}])
        self.assertEqual([p["name"] for p in row["testers"]], ["Tom QA"])
        self.assertEqual((row["bugs_linked"], row["escaped_defects"]), (1, 1))

    def test_integrity_flags(self):
        rows = self.rows()
        self.assertTrue(rows[102]["closed_directly"])
        self.assertEqual(rows[102]["outcome"], "closed_unreleased")
        self.assertIn("closed_without_code", rows[102]["flags"])
        self.assertEqual(rows[103]["skipped_stages"], ["qa", "qaa", "int"])
        self.assertIn("released_without_release_tag", rows[103]["flags"])
        self.assertIn("closed_unreleased_with_code", rows[104]["flags"])
        self.assertEqual(rows[104]["code_time_hours"], 22)  # first commit +8h -> merge +30h
        self.assertNotIn(105, rows)  # in progress: only with status=all
        progress = self.rows(status="all")[105]
        self.assertEqual((progress["outcome"], progress["stage"], progress["age_hours"]), ("in_progress", "qa", 699))

    def test_filters_summary_and_csv(self):
        rows = list(self.rows(status="all").values())
        self.assertEqual(sorted(r["id"] for r in rows), [101, 102, 103, 104, 105])
        self.assertEqual([r["id"] for r in build_story_lifecycles(self.db, StoryFilters(date_from=RANGE.date_from, date_to=RANGE.date_to, person="Tom QA"), TEAMS, CONFIG, now=NOW)], [101])
        self.assertEqual(self.rows(tags=["Release 2026.10.1"]).keys(), {101})
        summary = summarize_lifecycles(rows, CONFIG)
        self.assertEqual((summary["released"], summary["closed_unreleased"], summary["in_progress"]), (2, 2, 1))
        self.assertEqual(summary["first_time_right_pct"], 50.0)  # 103 yes, 101 bounced
        self.assertEqual(summary["qa_bounce_rate_pct"], 50.0)  # 101 bounced, 105 not (yet)
        self.assertEqual(summary["releases"], {"2026.10.1": 1})

        text = lifecycle_csv(rows, CONFIG)
        self.assertTrue(text.startswith("﻿story_id,title,type"))
        header = text.splitlines()[0]
        self.assertIn("qa_entered_at", header)
        self.assertIn("qaa_done_hours", header)
        self.assertIn("Alice Anderson", text)
        # Testing columns are placeholders until test data is integrated.
        self.assertIn("test_cases,test_pass_rate,coverage_pct", header)
        transitions = transitions_csv(rows, CONFIG).splitlines()
        self.assertIn("101,Story,QA Dev In Progress,Active,qa,dev,2026-10-05T09:00:00Z,Alice,24.0,true", transitions)
        self.assertIn("via Task #201", pull_requests_csv(rows))

    def test_api_and_csv_endpoints(self):
        app.dependency_overrides[get_db] = lambda: self.db
        try:
            client = TestClient(app)
            data = client.get("/api/stories/lifecycle?date_from=2026-10-01&date_to=2026-10-31&status=all").json()
            self.assertEqual(data["summary"]["stories"], 5)
            self.assertEqual(data["config"]["stages"][2]["states"], ["QA Dev In Progress"])
            csv_response = client.get("/api/reports/story-transitions.csv?date_from=2026-10-01&date_to=2026-10-31")
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn('filename="story-transitions-20261001-20261031.csv"', csv_response.headers["content-disposition"])
            self.assertEqual(client.get("/api/reports/nope.csv").status_code, 404)
        finally:
            app.dependency_overrides.clear()


class SyncAndSnapshotTests(unittest.TestCase):
    def test_history_is_downloaded_only_when_the_item_changed(self):
        db = _session()
        client = MagicMock(request_count=0)
        raw = {"id": 9, "project": "Shop", "work_item_type": "User Story", "title": "t", "state": "Active",
               "assigned_to_email": "alice@corp.com", "changed_date": "2026-10-02T09:00:00Z", "created_date": "2026-10-01T09:00:00Z", "tags": []}
        client.fetch_work_items.return_value = [raw]
        client.fetch_pipeline_runs.return_value = []
        client.fetch_work_item_updates.return_value = {
            "history": [{"rev": 1, "changed_at": "2026-10-01T09:00:00Z", "state": "New"}, {"rev": 2, "changed_at": "2026-10-02T09:00:00Z", "from_state": "New", "state": "Active"}],
            "relations": [],
        }
        config = DevOpsConfig(organization="corp", projects=["Shop"], pat="x")
        first = run_devops_ingestion(db, devops_config=config, client=client, config_path="/nonexistent.yaml")
        second = run_devops_ingestion(db, devops_config=config, client=client, config_path="/nonexistent.yaml")
        self.assertEqual((first.histories_fetched, second.histories_fetched, second.histories_unchanged), (1, 0, 1))
        self.assertEqual(client.fetch_work_item_updates.call_count, 1)
        self.assertEqual([h.state for h in db.get(AzureWorkItem, 9).history], ["New", "Active"])

    def test_snapshot_keeps_history_and_links(self):
        db = _session()
        _item(db, 1, FULL_FLOW[:3], relations=[{"rel": "ArtifactLink", "name": "GitHub Commit", "url": "vstfs:///GitHub/Commit/c%2Fabc", "target_id": None}])
        _pr(db, 1, state="OPEN", github_id=77, refs="1", branch="feature/1-x")
        db.commit()
        directory = tempfile.mkdtemp()
        save_snapshot(db, directory)
        target = _session()
        load_snapshot(target, resolve_snapshot(directory))
        item = target.get(AzureWorkItem, 1)
        self.assertEqual([h.state for h in item.history], ["New", "Active", "QA Dev In Progress"])
        self.assertEqual(item.relations[0].name, "GitHub Commit")
        pr = target.query(PullRequest).one()
        self.assertEqual((pr.state, pr.github_id, pr.work_item_refs, pr.head_branch), ("OPEN", 77, "1", "feature/1-x"))


if __name__ == "__main__":
    unittest.main()
