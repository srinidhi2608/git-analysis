import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.azure_devops import AzureDevOpsClient
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.dora_metrics import get_team_dora_metrics
from app.main import app
from app.models import Base, Developer, PullRequest, Repository
from app.queries import get_prs_per_week, get_target_branches
from app.time_window import TimeWindow, parse_bound

from tests.test_github_ingestion import _service

T0 = datetime(2026, 10, 12, 9, 0)


class ParseTests(unittest.TestCase):
    def test_dates_cover_whole_days_and_datetimes_are_exact_utc(self):
        self.assertEqual(parse_bound("2026-10-01"), datetime(2026, 10, 1))
        self.assertEqual(parse_bound("2026-10-01", is_end=True), datetime(2026, 10, 2))
        self.assertEqual(parse_bound("2026-10-01T09:30"), datetime(2026, 10, 1, 9, 30))
        self.assertEqual(parse_bound("2026-10-01T09:30:00Z"), datetime(2026, 10, 1, 9, 30))
        self.assertEqual(parse_bound("2026-10-01T15:00:00+05:30"), datetime(2026, 10, 1, 9, 30))

    def test_window_defaults_and_validation(self):
        window = TimeWindow.parse("2026-10-01T00:00Z", "2026-10-08T00:00Z")
        self.assertEqual(window.days, 7)
        self.assertTrue(window.contains(datetime(2026, 10, 7, 23, 59)))
        self.assertFalse(window.contains(datetime(2026, 10, 8)))
        self.assertAlmostEqual(TimeWindow.parse(None, None, default_days=14).days, 14, places=3)
        with self.assertRaises(ValueError):
            TimeWindow.parse("2026-10-08", "2026-10-01")
        with self.assertRaises(ValueError):
            TimeWindow.parse("not-a-date", None)


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    repo = Repository(name="org/shop", is_active=True)
    dev = Developer(github_username="alice")
    db.add_all([repo, dev])
    db.flush()
    # Merged on Oct 12 09:00, 12:00 and 18:00, and Oct 20.
    for number, merged in enumerate([T0, T0 + timedelta(hours=3), T0 + timedelta(hours=9), T0 + timedelta(days=8)], 1):
        db.add(PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=number, title="t", base_branch="main",
                           created_at=merged - timedelta(hours=1), merged_at=merged, cycle_time_minutes=60))
    db.commit()


class RangeQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        _seed(self.db)

    def test_queries_respect_start_and_end_times(self):
        morning = TimeWindow(datetime(2026, 10, 12, 7), datetime(2026, 10, 12, 13))
        self.assertEqual(get_team_dora_metrics(self.db, window=morning)["summary"]["merged_pull_request_count"], 2)
        october = TimeWindow(datetime(2026, 10, 1), datetime(2026, 11, 1))
        self.assertEqual(sum(w["mergedPrs"] for w in get_prs_per_week(self.db, window=october)), 4)
        self.assertEqual(get_target_branches(self.db, window=morning)["branches"], [{"name": "main", "pull_requests": 2}])

    def test_api_accepts_date_and_datetime_ranges(self):
        app.dependency_overrides[get_db] = lambda: self.db
        try:
            client = TestClient(app)
            day = client.get("/api/dora/team?date_from=2026-10-12&date_to=2026-10-12").json()["summary"]
            self.assertEqual(day["merged_pull_request_count"], 3)
            self.assertEqual(day["window_days"], 1)
            hours = client.get("/api/dora/team", params={"date_from": "2026-10-12T10:00:00Z", "date_to": "2026-10-12T20:00:00Z"})
            self.assertEqual(hours.json()["summary"]["merged_pull_request_count"], 2)
            weekly = client.get("/api/chart/prs-per-week?date_from=2026-10-19&date_to=2026-10-25").json()
            self.assertEqual(weekly, [{"week": "2026-W43", "mergedPrs": 1}])
            bad = client.get("/api/dora/team?date_from=2026-10-20&date_to=2026-10-01")
            self.assertEqual(bad.status_code, 400)
            sync = client.post("/api/ingest", json={"date_from": "2026-10-20T00:00:00Z", "date_to": "2026-10-01T00:00:00Z"})
            self.assertEqual(sync.status_code, 400)
        finally:
            app.dependency_overrides.clear()


class SyncRangeTests(unittest.TestCase):
    def test_github_sync_skips_prs_updated_after_the_range_and_stops_before_it(self):
        service = _service()
        service.since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        service.until = datetime(2026, 10, 10)  # naive = UTC
        detail_requests: list[dict] = []

        def fake_run_query(query, variables):
            if "pullRequests(" in query:
                return {"data": {"repository": {"pullRequests": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c"},
                    "nodes": [
                        {"number": 3, "updatedAt": "2026-10-15T00:00:00Z", "author": {"login": "a"}},
                        {"number": 2, "updatedAt": "2026-10-05T00:00:00Z", "author": {"login": "a"}},
                        {"number": 1, "updatedAt": "2026-09-20T00:00:00Z", "author": {"login": "a"}},
                    ],
                }}}}
            detail_requests.append(variables)
            return {"data": {"repository": {}}}

        service._run_query = fake_run_query  # type: ignore[method-assign]
        service._fetch_repo_prs("org", "repo")
        self.assertEqual([request["number0"] for request in detail_requests], [2])

    def test_azure_sync_sends_both_bounds(self):
        session = MagicMock()
        session.headers = {}
        response = MagicMock(status_code=200, ok=True, headers={"Content-Type": "application/json"})
        response.json.return_value = {"value": [], "workItems": []}
        session.request.return_value = response
        client = AzureDevOpsClient(DevOpsConfig(organization="corp", projects=["Shop"], pat="x"), session=session)
        since, until = datetime(2026, 10, 1, 8, 30), datetime(2026, 10, 2, 17, 0)

        client.fetch_pipeline_runs("Shop", since, until)
        params = session.request.call_args.kwargs["params"]
        self.assertEqual((params["minTime"], params["maxTime"]), ("2026-10-01T08:30:00Z", "2026-10-02T17:00:00Z"))

        client.fetch_work_items("Shop", since, until)
        wiql = session.request.call_args.kwargs["json"]["query"]
        self.assertIn("[System.ChangedDate] >= '2026-10-01T08:30:00Z'", wiql)
        self.assertIn("[System.ChangedDate] < '2026-10-02T17:00:00Z'", wiql)
        self.assertEqual(session.request.call_args.kwargs["params"]["timePrecision"], "true")

    def test_sync_endpoints_pass_the_range_to_the_background_job(self):
        with patch("app.main._background_ingest") as job:
            response = TestClient(app).post(
                "/api/ingest", json={"date_from": "2026-10-01T06:00:00+02:00", "date_to": "2026-10-03T00:00:00Z"}
            )
        self.assertEqual(response.status_code, 202)
        window = job.call_args.args[0]
        self.assertEqual((window.start, window.end), (datetime(2026, 10, 1, 4), datetime(2026, 10, 3)))
        from app.main import _ingestion_lock

        if _ingestion_lock.locked():  # the mocked job never releases it
            _ingestion_lock.release()


if __name__ == "__main__":
    unittest.main()
