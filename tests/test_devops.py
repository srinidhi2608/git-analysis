import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.azure_devops import AzureDevOpsClient, AzureDevOpsError, split_tags
from app.devops_config import DevOpsConfig, parse_devops_config
from app.devops_ingest import parse_azure_datetime, run_devops_ingestion, upsert_pipeline_run, upsert_work_item
from app.devops_metrics import (
    StoryFilters,
    compute_dora,
    list_people,
    unlinked_azure_identities,
    markdown_report,
    people_efficiency,
    query_stories,
    stories_csv,
    summarize_stories,
)
from app.models import AzurePipelineRun, AzureWorkItem, Base, Commit, Developer, PullRequest, Repository
from app.snapshot import load_snapshot, resolve_snapshot, save_snapshot
from app.teams import parse_team_config

TEAMS = parse_team_config(
    {
        "teams": {
            "Growth": [
                {"name": "alice", "azure": "alice@corp.com", "role": "developer"},
                {"name": "tina", "azure": ["tina@corp.com", "Tina Tester"], "role": "tester"},
            ],
            "Payments": [{"name": "bob", "azure": "bob@corp.com", "role": "developer"}],
        }
    }
)
CONFIG = DevOpsConfig(
    organization="corp",
    projects=["Shop"],
    deployment_pattern="(?i)deploy",
    production_pattern="(?i)prod",
    pat="secret",
)
DAY = date(2026, 10, 12)  # a Monday
T0 = datetime(2026, 10, 12, 9, 0)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _response(json_body, headers=None, status=200):
    response = MagicMock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
    response.json.return_value = json_body
    response.text = str(json_body)
    response.raise_for_status = MagicMock()
    return response


def _work_item(db, item_id, *, assigned, created_by=None, closed=None, activated=None, created=None,
               item_type="User Story", tags=(), points=None, title="Story"):
    upsert_work_item(
        db,
        {
            "id": item_id,
            "project": "Shop",
            "work_item_type": item_type,
            "title": title,
            "state": "Closed" if closed else "Active",
            "assigned_to": assigned.split("@")[0].title() if assigned else None,
            "assigned_to_email": assigned,
            "created_by": None,
            "created_by_email": created_by,
            "created_date": created or T0 - timedelta(days=10),
            "activated_date": activated,
            "closed_date": closed,
            "changed_date": closed or T0,
            "story_points": points,
            "tags": list(tags),
            "url": f"https://dev.azure.com/corp/Shop/_workitems/edit/{item_id}",
        },
    )


def _run(db, build_id, name, result, finish, *, repo="org/shop", sha=None, definition_id=1,
         requested_for=None, requested_email=None, branch="refs/heads/main"):
    upsert_pipeline_run(
        db,
        {
            "project": "Shop",
            "build_id": build_id,
            "definition_id": definition_id,
            "definition_name": name,
            "status": "completed",
            "result": result,
            "source_branch": branch,
            "source_version": sha,
            "repository_name": repo,
            "repository_type": "GitHub",
            "requested_for": requested_for,
            "requested_for_email": requested_email,
            "finish_time": finish,
        },
    )


def _pr(db, number, author, merged, *, sha=None, first_commit=None, title="Change"):
    repo = db.query(Repository).filter_by(name="org/shop").one_or_none()
    if repo is None:
        repo = Repository(name="org/shop", is_active=True)
        db.add(repo)
        db.flush()
    dev = db.query(Developer).filter_by(github_username=author).one_or_none()
    if dev is None:
        dev = Developer(github_username=author, team_name=TEAMS.team_for(author))
        db.add(dev)
        db.flush()
    pr = PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=number, title=title,
                     created_at=merged - timedelta(hours=10), merged_at=merged, merge_commit_sha=sha)
    db.add(pr)
    db.flush()
    if first_commit:
        db.add(Commit(pr_id=pr.id, developer_id=dev.id, commit_hash=f"c{number}", message="m", committed_at=first_commit))
    return pr


class ClientTests(unittest.TestCase):
    def test_work_items_use_wiql_then_batches_and_are_normalised(self):
        session = MagicMock()
        session.headers = {}
        session.request.side_effect = [
            _response({"workItems": [{"id": i} for i in range(1, 251)]}),
            _response({"value": [{"id": 1, "fields": {
                "System.WorkItemType": "User Story", "System.Title": "Login", "System.State": "Closed",
                "System.AssignedTo": {"displayName": "Alice A", "uniqueName": "Alice@Corp.com"},
                "System.CreatedBy": "Tina Tester <tina@corp.com>",
                "System.Tags": "checkout; Release-1 ;checkout",
                "System.CreatedDate": "2026-10-01T10:00:00.1234567Z",
                "Microsoft.VSTS.Common.ClosedDate": "2026-10-05T10:00:00Z",
                "Microsoft.VSTS.Scheduling.StoryPoints": 5,
            }}]}),
            _response({"value": []}),
        ]
        client = AzureDevOpsClient(CONFIG, session=session)
        items = client.fetch_work_items("Shop", datetime(2026, 9, 1))

        wiql_call, first_batch, second_batch = session.request.call_args_list
        self.assertIn("/Shop/_apis/wit/wiql", wiql_call.args[1])
        self.assertIn("[System.ChangedDate] >= '2026-09-01T00:00:00Z'", wiql_call.kwargs["json"]["query"])
        self.assertEqual(len(first_batch.kwargs["json"]["ids"]), 200)
        self.assertEqual(len(second_batch.kwargs["json"]["ids"]), 50)
        item = items[0]
        self.assertEqual(item["assigned_to_email"], "alice@corp.com")
        self.assertEqual(item["created_by_email"], "tina@corp.com")
        self.assertEqual(item["tags"], ["checkout", "Release-1"])
        self.assertEqual(item["story_points"], 5.0)
        self.assertEqual(item["url"], "https://dev.azure.com/corp/Shop/_workitems/edit/1")
        self.assertEqual(client.request_count, 3)

    def test_pipeline_runs_follow_continuation_tokens(self):
        session = MagicMock()
        session.headers = {}
        run = {"id": 7, "definition": {"id": 3, "name": "Deploy Prod"}, "result": "succeeded",
               "repository": {"name": "org/shop", "type": "GitHub"}, "sourceVersion": "abc",
               "finishTime": "2026-10-12T10:00:00Z", "_links": {"web": {"href": "https://x/7"}}}
        session.request.side_effect = [
            _response({"value": [run]}, headers={"x-ms-continuationtoken": "next"}),
            _response({"value": [{**run, "id": 8}]}),
        ]
        runs = AzureDevOpsClient(CONFIG, session=session).fetch_pipeline_runs("Shop", datetime(2026, 9, 1))
        self.assertEqual([r["build_id"] for r in runs], [7, 8])
        self.assertEqual(session.request.call_args_list[1].kwargs["params"]["continuationToken"], "next")
        self.assertEqual(runs[0]["definition_name"], "Deploy Prod")
        self.assertEqual(runs[0]["url"], "https://x/7")

    def test_invalid_pat_sign_in_page_raises_clear_error(self):
        session = MagicMock()
        session.headers = {}
        session.request.return_value = _response({}, headers={"Content-Type": "text/html"}, status=203)
        with self.assertRaises(AzureDevOpsError) as ctx:
            AzureDevOpsClient(CONFIG, session=session).fetch_pipeline_runs("Shop", datetime(2026, 9, 1))
        self.assertIn("AZURE_DEVOPS_PAT", str(ctx.exception))


class ParsingTests(unittest.TestCase):
    def test_azure_datetimes_with_seven_fraction_digits(self):
        self.assertEqual(parse_azure_datetime("2026-10-01T10:00:00.1234567Z"), datetime(2026, 10, 1, 10, 0, 0, 123456))
        self.assertEqual(parse_azure_datetime("2026-10-01T10:00:00Z"), datetime(2026, 10, 1, 10, 0))
        self.assertIsNone(parse_azure_datetime(None))

    def test_config_parsing_and_patterns(self):
        config = parse_devops_config(
            {"azure_devops": {"organization": "corp", "projects": "Shop", "production_pattern": "(?i)prod",
                              "incidents": {"work_item_types": ["Bug"], "tags": ["Incident"]}}},
            pat="x",
        )
        self.assertEqual(config.organization_url, "https://dev.azure.com/corp")
        self.assertEqual(config.projects, ["Shop"])
        self.assertTrue(config.configured)
        self.assertTrue(config.is_deployment("Deploy API") and config.is_production("Deploy API Prod"))
        self.assertFalse(config.is_production("Deploy API Staging"))
        self.assertTrue(config.is_incident("Bug", ["incident"]))
        self.assertFalse(config.is_incident("Bug", ["ui"]))
        self.assertEqual(parse_devops_config({}, pat="").missing()[0], "azure_devops.organization in config.yaml")
        self.assertEqual(split_tags(" a; b ;A; "), ["a", "b"])

    def test_azure_identities_map_to_github_logins(self):
        self.assertEqual(TEAMS.login_for_azure("ALICE@corp.com"), "alice")
        self.assertEqual(TEAMS.login_for_azure(None, "Tina Tester"), "tina")
        self.assertEqual(TEAMS.login_for_azure("bob@other.org"), "bob")  # email local part = login
        self.assertIsNone(TEAMS.login_for_azure("mallory@corp.com"))
        self.assertEqual(TEAMS.role_for("tina"), "tester")


class SyncTests(unittest.TestCase):
    def test_sync_skips_work_items_of_non_members_and_keeps_pipeline_runs(self):
        db = _session()
        client = MagicMock()
        client.request_count = 4
        client.fetch_work_items.return_value = [
            {"id": 1, "project": "Shop", "work_item_type": "User Story", "title": "A", "assigned_to_email": "alice@corp.com", "tags": ["x"]},
            {"id": 2, "project": "Shop", "work_item_type": "Bug", "title": "B", "assigned_to_email": None, "created_by_email": "tina@corp.com", "tags": []},
            {"id": 3, "project": "Shop", "work_item_type": "User Story", "title": "C", "assigned_to_email": "mallory@corp.com", "tags": []},
        ]
        client.fetch_pipeline_runs.return_value = [
            {"project": "Shop", "build_id": 9, "definition_name": "Deploy Prod", "result": "succeeded", "finish_time": "2026-10-12T10:00:00Z"}
        ]
        config_file = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        config_file.write("teams:\n  Growth:\n    - name: alice\n      azure: alice@corp.com\n    - name: tina\n      azure: tina@corp.com\n")
        config_file.close()
        self.addCleanup(os.unlink, config_file.name)

        with self.assertLogs("app.teams", level="WARNING") as logs:
            stats = run_devops_ingestion(db, config_path=config_file.name, devops_config=CONFIG, client=client)

        self.assertEqual((stats.work_items, stats.skipped_work_items, stats.pipeline_runs), (2, 1, 1))
        self.assertEqual(sorted(item.id for item in db.query(AzureWorkItem)), [1, 2])
        self.assertEqual(db.query(AzurePipelineRun).one().finish_time, datetime(2026, 10, 12, 10, 0))
        self.assertIn("mallory@corp.com", "\n".join(logs.output))

        # Re-sync updates in place (tags replaced, no duplicates).
        client.fetch_work_items.return_value = [{**client.fetch_work_items.return_value[0], "tags": ["y"]}]
        run_devops_ingestion(db, config_path=config_file.name, devops_config=CONFIG, client=client)
        self.assertEqual([t.tag for t in db.get(AzureWorkItem, 1).tags], ["y"])
        self.assertEqual(db.query(AzurePipelineRun).count(), 1)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.db = db = _session()
        # Stories closed in the week of DAY
        _work_item(db, 101, assigned="alice@corp.com", closed=T0, activated=T0 - timedelta(days=2), tags=["checkout", "release-1"], points=5, title="Checkout")
        _work_item(db, 102, assigned="alice@corp.com", closed=T0 + timedelta(days=1), activated=T0 - timedelta(days=1), tags=["checkout"], points=3)
        _work_item(db, 103, assigned="bob@corp.com", closed=T0 + timedelta(days=2), tags=["release-1"], points=2)
        _work_item(db, 104, assigned="tina@corp.com", closed=T0 + timedelta(days=2), item_type="Task", tags=["testing"])
        _work_item(db, 105, assigned="alice@corp.com", closed=T0 - timedelta(days=40), tags=["checkout"])  # outside range
        _work_item(db, 106, assigned="bob@corp.com", closed=None, tags=["checkout"])  # open
        _work_item(db, 107, assigned="mallory@corp.com", closed=T0, tags=["checkout"])  # not a member
        for i in range(3):  # bugs reported by the tester
            _work_item(db, 200 + i, assigned=None, created_by="tina@corp.com", item_type="Bug", created=T0 + timedelta(hours=i))
        db.commit()
        self.filters = StoryFilters(date_from=DAY, date_to=DAY + timedelta(days=6))

    def test_tag_person_team_and_status_filters(self):
        ids = lambda **kw: [s["id"] for s in query_stories(self.db, StoryFilters(**{"date_from": DAY, "date_to": DAY + timedelta(days=6), **kw}), TEAMS)]
        self.assertEqual(sorted(ids()), [101, 102, 103, 104])  # mallory's item excluded: not in any team
        self.assertEqual(sorted(ids(tags=["checkout"])), [101, 102])
        self.assertEqual(sorted(ids(tags=["checkout", "release-1"])), [101, 102, 103])
        self.assertEqual(ids(tags=["checkout", "release-1"], tag_mode="all"), [101])
        self.assertEqual(sorted(ids(person="alice")), [101, 102])
        self.assertEqual(sorted(ids(team="Payments")), [103])
        self.assertEqual(sorted(ids(team="Payments", status="all")), [103, 106])
        self.assertEqual(ids(types=["Task"]), [104])

    def test_summary_efficiency_and_reports(self):
        stories = query_stories(self.db, self.filters, TEAMS)
        summary = summarize_stories(stories, self.filters)
        self.assertEqual(summary["completed"], 4)
        self.assertEqual(summary["story_points_completed"], 10)
        self.assertEqual(summary["throughput_per_week"], 4.0)

        efficiency = {row["person"]: row for row in people_efficiency(self.db, stories, self.filters, TEAMS)}
        self.assertEqual(efficiency["alice"]["completed"], 2)
        self.assertEqual(efficiency["alice"]["story_points"], 8)
        self.assertEqual(efficiency["alice"]["share_of_completed_pct"], 50.0)
        self.assertEqual(efficiency["alice"]["median_cycle_time_days"], 2.0)
        self.assertEqual(efficiency["tina"]["role"], "tester")
        self.assertEqual(efficiency["tina"]["bugs_reported"], 3)
        self.assertNotIn("Mallory", efficiency)

        csv_text = stories_csv(stories)
        self.assertTrue(csv_text.startswith("ID,Type,Title"))
        self.assertIn("checkout; release-1", csv_text)
        report = markdown_report(self.filters, summary, list(efficiency.values()), stories, None)
        self.assertIn("# DevOps Integration report", report)
        self.assertIn("| alice | developer | Growth | 2 |", report)
        self.assertIn("[101](https://dev.azure.com/corp/Shop/_workitems/edit/101)", report)

    def test_linked_prs_via_ab_mentions(self):
        _pr(self.db, 1, "alice", T0, title="AB#101 checkout flow")
        self.db.commit()
        story = next(s for s in query_stories(self.db, self.filters, TEAMS) if s["id"] == 101)
        self.assertEqual(story["linked_prs"][0]["pr_number"], 1)


class DoraTests(unittest.TestCase):
    def setUp(self):
        self.db = db = _session()
        # Daily successful production deploys Mon-Fri, one failure (recovered 2h later), staging ignored.
        for i in range(5):
            _run(db, 10 + i, "Deploy Prod", "succeeded", T0 + timedelta(days=i, hours=8), sha=f"sha{i}")
        _run(db, 30, "Deploy Prod", "failed", T0 + timedelta(days=1, hours=6))
        _run(db, 40, "Deploy Staging", "succeeded", T0 + timedelta(hours=1))
        _run(db, 50, "Build CI", "failed", T0 + timedelta(hours=2))
        # PR merged with the deployed commit sha -> lead time from first commit
        _pr(db, 1, "alice", T0 + timedelta(hours=2), sha="sha0", first_commit=T0 - timedelta(hours=10))
        # PR without matching sha -> first deployment of the repo after merge (day 2 deploy)
        _pr(db, 2, "bob", T0 + timedelta(days=1, hours=1), sha="unknown", first_commit=T0 + timedelta(days=1))
        db.commit()
        self.range = (DAY, DAY + timedelta(days=6))

    def test_team_level_dora_from_pipelines(self):
        dora = compute_dora(self.db, CONFIG, TEAMS, *self.range)
        self.assertEqual(dora["method"], "pipeline")
        self.assertEqual(dora["deployment_frequency"]["deployments"], 5)
        self.assertEqual(dora["deployment_frequency"]["median_deployment_days_per_week"], 5)
        self.assertEqual(dora["deployment_frequency"]["level"], "Elite")
        # PR1: first commit T0-10h -> deploy T0+8h = 18h; PR2: T0+1d -> deploy day 2 (T0+1d+8h) = 8h
        self.assertEqual(dora["lead_time"]["pull_requests"], 2)
        self.assertEqual(dora["lead_time"]["median_hours"], 13.0)
        self.assertEqual(dora["lead_time"]["level"], "Elite")
        self.assertEqual((dora["change_failure_rate"]["failed"], dora["change_failure_rate"]["total"]), (1, 6))
        self.assertEqual(dora["change_failure_rate"]["level"], "Low")  # 16.7%
        self.assertEqual(dora["recovery_time"]["median_hours"], 2.0)
        self.assertEqual(dora["recovery_time"]["level"], "High")
        self.assertNotIn("Build CI", dora["pipelines"])

    def test_person_scope_uses_deployments_that_shipped_their_prs(self):
        dora = compute_dora(self.db, CONFIG, TEAMS, *self.range, person="bob")
        self.assertEqual(dora["scope"]["kind"], "person")
        self.assertEqual(dora["deployment_frequency"]["deployments"], 1)
        self.assertEqual(dora["lead_time"]["median_hours"], 8.0)
        # bob's first deployment attempt after merge failed (day 1 +6h); the next one shipped his PR.
        self.assertEqual((dora["change_failure_rate"]["failed"], dora["change_failure_rate"]["total"]), (1, 2))
        team = compute_dora(self.db, CONFIG, TEAMS, *self.range, team="Growth")
        self.assertEqual(team["lead_time"]["pull_requests"], 1)

    def test_incident_mode_links_incidents_to_previous_deployment(self):
        config = DevOpsConfig(**{**CONFIG.__dict__, "incident_work_item_types": ["Bug"], "incident_tags": ["Incident"]})
        _work_item(self.db, 900, assigned="bob@corp.com", item_type="Bug", tags=["Incident"],
                   created=T0 + timedelta(days=2, hours=9), closed=T0 + timedelta(days=2, hours=12))
        self.db.commit()
        dora = compute_dora(self.db, config, TEAMS, *self.range)
        self.assertEqual(dora["method"], "incidents")
        self.assertEqual((dora["change_failure_rate"]["failed"], dora["change_failure_rate"]["total"]), (1, 5))
        self.assertEqual(dora["change_failure_rate"]["rate"], 20.0)
        # deploy day 2 at +8h -> incident closed day 2 at +12h
        self.assertEqual(dora["recovery_time"]["median_hours"], 4.0)


class PersonRunAttributionTests(unittest.TestCase):
    """Filtering DORA by person/team must keep every pipeline run that belongs to them."""

    def setUp(self):
        self.db = db = _session()
        # Azure Repos name the repository "shop"; GitHub calls it "org/shop".
        _pr(db, 1, "alice", T0, sha="m1", first_commit=T0 - timedelta(hours=4))
        _pr(db, 2, "bob", T0 + timedelta(days=1), sha="m2")
        db.add(Commit(pr_id=2, developer_id=2, commit_hash="b-head", message="m", committed_at=T0))
        # Deployment of a later commit of the repo -> ships alice's PR (repo name differs).
        _run(db, 1, "Deploy Prod", "succeeded", T0 + timedelta(hours=3), repo="shop", sha="later")
        # Manual production deploy requested by alice (no PR link at all).
        _run(db, 2, "Deploy Prod", "failed", T0 + timedelta(days=2), repo="infra", sha="x",
             requested_for="Alice", requested_email="alice@corp.com")
        # CI build of bob's PR head commit, and his PR validation build.
        _run(db, 3, "Build CI", "succeeded", T0 + timedelta(hours=1), sha="b-head")
        _run(db, 4, "PR validation", "failed", T0 + timedelta(hours=2), sha="preview", branch="refs/pull/2/merge")
        # Someone else's run.
        _run(db, 5, "Build CI", "succeeded", T0 + timedelta(hours=5), sha="zzz", requested_for="Stranger")
        db.commit()
        self.range = (DAY, DAY + timedelta(days=6))

    def test_person_gets_requested_runs_and_deployments_across_repo_names(self):
        dora = compute_dora(self.db, CONFIG, TEAMS, *self.range, person="alice")
        runs = {run["id"]: run for run in dora["runs"]}
        self.assertEqual(sorted(runs), [1, 2])
        self.assertIn("shipped org/shop#1", runs[1]["attributed_by"])
        self.assertEqual(runs[2]["requested_for"], "alice")
        self.assertTrue(runs[2]["is_production_deployment"])
        self.assertEqual(dora["deployment_frequency"]["deployments"], 1)
        self.assertEqual(dora["lead_time"]["median_hours"], 7.0)
        # The failed manual deployment counts against alice.
        self.assertEqual((dora["change_failure_rate"]["failed"], dora["change_failure_rate"]["total"]), (1, 2))

    def test_person_gets_ci_and_pr_validation_builds_of_their_prs(self):
        dora = compute_dora(self.db, CONFIG, TEAMS, *self.range, person="bob")
        runs = {run["id"]: run["attributed_by"] for run in dora["runs"]}
        self.assertEqual(sorted(runs), [3, 4])
        self.assertEqual(runs[3], ["built org/shop#2"])
        self.assertEqual(runs[4], ["validated org/shop#2"])

    def test_team_and_everyone_scopes(self):
        growth = compute_dora(self.db, CONFIG, TEAMS, *self.range, team="Growth")
        self.assertEqual(sorted(run["id"] for run in growth["runs"]), [1, 2])
        everyone = compute_dora(self.db, CONFIG, TEAMS, *self.range)
        self.assertEqual(everyone["runs_total"], 5)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_includes_azure_data(self):
        db = _session()
        _work_item(db, 1, assigned="alice@corp.com", closed=T0, tags=["a", "b"])
        _run(db, 5, "Deploy Prod", "succeeded", T0, sha="abc")
        db.commit()
        directory = tempfile.mkdtemp()
        saved = save_snapshot(db, directory)
        self.assertEqual(saved["counts"]["azure_work_items"], 1)

        target = _session()
        load_snapshot(target, resolve_snapshot(directory))
        item = target.get(AzureWorkItem, 1)
        self.assertEqual(sorted(t.tag for t in item.tags), ["a", "b"])
        self.assertEqual(item.closed_date, T0)
        self.assertEqual(target.query(AzurePipelineRun).one().source_version, "abc")



IDENTITIES = parse_team_config(
    {
        "teams": {
            "Growth": [
                {"name": "Vaishnavi K", "github": "CF-Vaishnavi", "azure_devops": "vaishnavi.k@company.com", "role": "developer"},
                {"name": "Tom QA", "azure_devops": ["tom.q@company.com", "Tom Quinn"], "role": "tester"},
                {"name": "Sinchana Bhat", "github": "CF-SinchanaBhat"},  # matched by Azure display name
            ],
            "Legacy": ["plain-login", {"name": "old-style", "azure": "old@company.com"}],
        }
    }
)


class IdentityMappingTests(unittest.TestCase):
    def test_members_have_display_name_github_and_azure_identities(self):
        vaishnavi = IDENTITIES.member("cf-vaishnavi")
        self.assertEqual((vaishnavi.key, vaishnavi.display_name, vaishnavi.github), ("CF-Vaishnavi", "Vaishnavi K", "CF-Vaishnavi"))
        self.assertEqual(IDENTITIES.display_name("CF-Vaishnavi"), "Vaishnavi K")
        self.assertEqual(IDENTITIES.display_name("not-configured"), "not-configured")
        # A tester without GitHub: keyed by display name, never matches GitHub data.
        tom = IDENTITIES.member("Tom QA")
        self.assertIsNone(tom.github)
        self.assertFalse(IDENTITIES.is_member("Tom QA"))
        self.assertTrue(IDENTITIES.is_member("cf-vaishnavi"))
        # Legacy formats: name is the GitHub login, `azure` alias still works.
        self.assertTrue(IDENTITIES.is_member("plain-login"))
        self.assertEqual(IDENTITIES.login_for_azure("OLD@company.com"), "old-style")
        self.assertEqual(
            [m["key"] for m in IDENTITIES.members()],
            ["CF-Vaishnavi", "Tom QA", "CF-SinchanaBhat", "plain-login", "old-style"],
        )

    def test_azure_identities_resolve_to_the_same_person(self):
        self.assertEqual(IDENTITIES.member_for_azure("vaishnavi.k@company.com").key, "CF-Vaishnavi")
        self.assertEqual(IDENTITIES.member_for_azure(None, "Tom Quinn").key, "Tom QA")
        self.assertEqual(IDENTITIES.member_for_azure("unknown@company.com", "Sinchana Bhat").key, "CF-SinchanaBhat")
        self.assertIsNone(IDENTITIES.member_for_azure("stranger@company.com", "Stranger"))

    def test_same_azure_identity_for_two_people_keeps_the_first(self):
        with self.assertLogs("app.teams", level="WARNING"):
            config = parse_team_config(
                {"teams": {"A": [{"name": "One", "github": "one", "azure_devops": "x@c.com"}, {"name": "Two", "github": "two", "azure_devops": "X@c.com"}]}}
            )
        self.assertEqual(config.login_for_azure("x@c.com"), "one")

    def test_pages_use_display_names_and_report_unlinked_identities(self):
        db = _session()
        _work_item(db, 1, assigned="vaishnavi.k@company.com", closed=T0, points=3)
        _work_item(db, 2, assigned="tom.q@company.com", closed=T0)
        _work_item(db, 3, assigned="stranger@company.com", closed=T0)
        _work_item(db, 4, assigned=None, created_by="tom.q@company.com", item_type="Bug", created=T0)
        _pr(db, 1, "CF-Vaishnavi", T0)
        db.commit()
        filters = StoryFilters(date_from=DAY, date_to=DAY + timedelta(days=6))

        stories = query_stories(db, filters, IDENTITIES)
        self.assertEqual(sorted(s["assignee"] for s in stories), ["Tom QA", "Vaishnavi K"])
        efficiency = {row["person"]: row for row in people_efficiency(db, stories, filters, IDENTITIES)}
        self.assertEqual(efficiency["Vaishnavi K"]["prs_merged"], 1)
        self.assertEqual(efficiency["Vaishnavi K"]["github"], "CF-Vaishnavi")
        self.assertEqual(efficiency["Tom QA"]["bugs_reported"], 1)
        self.assertIsNone(efficiency["Tom QA"]["github"])

        # Filtering by a person uses their key; their GitHub PRs feed person-level DORA.
        self.assertEqual([s["id"] for s in query_stories(db, StoryFilters(date_from=DAY, date_to=DAY + timedelta(days=6), person="CF-Vaishnavi"), IDENTITIES)], [1])
        self.assertEqual([s["id"] for s in query_stories(db, StoryFilters(date_from=DAY, date_to=DAY + timedelta(days=6), person="Tom QA"), IDENTITIES)], [2])
        dora = compute_dora(db, CONFIG, IDENTITIES, DAY, DAY + timedelta(days=6), person="CF-Vaishnavi")
        self.assertEqual(dora["scope"]["person_label"], "Vaishnavi K")

        people = {p["key"]: p for p in list_people(db, IDENTITIES)}
        self.assertEqual(people["CF-Vaishnavi"]["label"], "Vaishnavi K")
        self.assertEqual(people["Tom QA"]["azure"], "tom.q@company.com, Tom Quinn")
        unlinked = unlinked_azure_identities(db, IDENTITIES)
        self.assertEqual([u["identity"] for u in unlinked], ["stranger@company.com"])


if __name__ == "__main__":
    unittest.main()
