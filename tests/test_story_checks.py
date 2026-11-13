import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import story_analyzer, story_splitter
from app.config import settings
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_history, upsert_work_item
from app.lifecycle_config import parse_lifecycle_config
from app.llm_client import LLMUnavailableError
from app.main import app
from app.models import Base, Developer, PullRequest, Repository
from app.pr_code import code_prompt_payload, fetch_pr_changes
from app.story_analyzer import analyze_story
from app.story_checks import heuristic_implementation, heuristic_test_coverage
from app.story_context import ContextOptions
from app.story_splitter import heuristic_split, split_requirement
from app.teams import parse_team_config
from app.testrail import (
    TestRailClient,
    TestRailError,
    case_ids_in,
    gather_test_cases,
    parse_testrail_config,
)

LIFECYCLE = parse_lifecycle_config({})
TEAMS = parse_team_config({})
T0 = datetime(2026, 10, 1, 9)
NOW = datetime(2026, 10, 30, 12)
CRITERIA = (
    "<ul><li>Given a saved card, when the customer pays, then the CVV is required.</li>"
    "<li>Given 5 saved cards, when the customer saves another, then an error asks to remove one.</li></ul>"
)


def _response(body, status=200, headers=None):
    response = MagicMock(status_code=status, headers=headers or {})
    response.json.return_value = body
    response.text = json.dumps(body)
    response.raise_for_status = MagicMock()
    return response


class PrCodeTests(unittest.TestCase):
    def test_files_are_paged_filtered_and_marked(self):
        page1 = [{"filename": f"src/f{i}.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "+x"} for i in range(98)]
        page1 += [{"filename": "package-lock.json", "patch": "+lock"}, {"filename": "img/logo.png"}]
        page2 = [{"filename": "tests/test_cvv.py", "status": "added", "additions": 20, "deletions": 0, "patch": "+def test_cvv(): ..."}]
        session = MagicMock()
        session.get.side_effect = [_response(page1), _response(page2)]
        result = fetch_pr_changes("org/shop", 7, session=session)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["files"]), 99)
        self.assertEqual(result["skipped_files"], ["package-lock.json", "img/logo.png"])
        self.assertTrue(next(f for f in result["files"] if f["filename"] == "tests/test_cvv.py")["is_test"])
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["page"], 2)
        self.assertIn("Bearer", session.get.call_args.kwargs["headers"]["Authorization"])

    def test_errors_and_budget(self):
        session = MagicMock()
        session.get.return_value = _response({}, status=404)
        self.assertIn("not found", fetch_pr_changes("org/shop", 1, session=session)["error"])
        session.get.return_value = _response({}, status=403)
        self.assertIn("GITHUB_TOKEN", fetch_pr_changes("org/shop", 1, session=session)["error"])
        big = [{"repository": "org/shop", "number": 1, "error": None, "skipped_files": [],
                "files": [{"filename": f"a{i}.py", "status": "modified", "additions": 1, "deletions": 0, "is_test": i == 0, "patch": "+" + "x" * 20_000} for i in range(80)]}]
        payload = code_prompt_payload(big)
        self.assertLessEqual(sum(len(f["patch"]) for f in payload[0]["files"]), 42_000)
        self.assertTrue(payload[0]["files"][-1]["is_test"])  # tests come after code
        self.assertIn("budget", payload[0]["files"][-1]["patch"])


class FakeTestRail:
    """Answers TestRail API URLs from a dict of endpoint -> body."""

    def __init__(self, routes):
        self.routes, self.calls, self.auth, self.headers = routes, [], None, {}

    def get(self, url, timeout=None):
        endpoint = url.split("index.php?/api/v2/", 1)[1]
        self.calls.append(endpoint)
        for prefix, body in self.routes.items():
            if endpoint.startswith(prefix):
                return _response(body() if callable(body) else body)
        return _response({"error": "unknown"}, status=400)


TESTRAIL = parse_testrail_config({"testrail": {"url": "https://acme.testrail.io/", "user": "qa@acme.dev", "project_ids": [1], "reference_format": "AB#{id}", "runs_to_scan": 5}}, api_key="key")


def _testrail_routes():
    return {
        "get_cases/1&refs=AB%231011": {"offset": 0, "size": 2, "_links": {"next": None}, "cases": [
            {"id": 11, "title": "Pay with saved card asks for CVV", "refs": "AB#1011, AB#99",
             "custom_steps_separated": [{"content": "Pay with saved card", "expected": "CVV is required"}]},
            {"id": 12, "title": "Unrelated", "refs": "AB#10110"},  # substring match only: dropped
        ]},
        "get_case/77": {"id": 77, "title": "Saved card limit", "refs": "", "custom_preconds": "5 cards saved", "custom_steps": "Save a 6th card", "custom_expected": "Error shown"},
        "get_statuses": [{"id": 1, "label": "Passed"}, {"id": 5, "label": "Failed"}],
        "get_runs/1": {"runs": [{"id": 3, "name": "Sprint 41", "created_on": 100}, {"id": 4, "name": "Sprint 42", "created_on": 200}]},
        "get_tests/4": {"tests": [{"id": 401, "case_id": 11, "status_id": 1}], "_links": {"next": None}},
        "get_tests/3": {"tests": [{"id": 301, "case_id": 11, "status_id": 5}, {"id": 302, "case_id": 77, "status_id": 5}], "_links": {"next": None}},
        "get_results/401": {"results": [{"comment": "Works, see video", "created_on": 210}]},
        "get_results/302": {"results": [{"comment": "Error not shown", "created_on": 150}]},
        "get_attachments_for_test/401": {"attachments": [{"name": "cvv.mp4"}]},
        "get_attachments_for_test/302": {"attachments": []},
        "get_attachments_for_case/11": [{"name": "design.png"}],
        "get_attachments_for_case/77": [],
    }


class TestRailTests(unittest.TestCase):
    def test_config_and_mentions(self):
        self.assertTrue(TESTRAIL.configured)
        self.assertEqual(TESTRAIL.url, "https://acme.testrail.io")
        self.assertEqual(TESTRAIL.reference(1011), "AB#1011")
        self.assertEqual(set(parse_testrail_config({}, api_key="").missing()), {"testrail.url in config.yaml", "testrail.user in config.yaml", "testrail.project_ids in config.yaml", "TESTRAIL_API_KEY in .env"})
        self.assertEqual(case_ids_in(["see https://acme.testrail.io/index.php?/cases/view/77", "C123 and ABC12 and C9"]), {77, 123})

    def test_cases_results_and_evidence(self):
        session = FakeTestRail(_testrail_routes())
        result = gather_test_cases(TESTRAIL, 1011, ["Covered by C77"], client=TestRailClient(TESTRAIL, session=session))
        cases = {case["id"]: case for case in result["cases"]}
        self.assertEqual(sorted(cases), [11, 77])
        self.assertEqual(cases[11]["linked_by"], "References")
        self.assertEqual(cases[77]["linked_by"], "link on the story")
        self.assertEqual(cases[11]["steps"], [{"step": "Pay with saved card", "expected": "CVV is required"}])
        self.assertEqual(cases[77]["preconditions"], "5 cards saved")
        self.assertEqual(cases[77]["steps"], [{"step": "Save a 6th card", "expected": "Error shown"}])
        # Newest run wins: C11 passed in Sprint 42 (it failed earlier in Sprint 41).
        self.assertEqual((cases[11]["latest"]["status"], cases[11]["latest"]["run"]), ("Passed", "Sprint 42"))
        self.assertEqual(cases[11]["latest"]["attachments"], ["cvv.mp4"])
        self.assertEqual(cases[11]["attachments"], ["design.png"])
        self.assertEqual(cases[77]["latest"]["status"], "Failed")
        self.assertEqual(cases[11]["url"], "https://acme.testrail.io/index.php?/cases/view/11")
        self.assertEqual(result["errors"], [])

    def test_errors_are_reported_not_raised(self):
        session = MagicMock()
        session.get.return_value = _response({"error": "no"}, status=401)
        result = gather_test_cases(TESTRAIL, 1011, [], client=TestRailClient(TESTRAIL, session=session))
        self.assertEqual(result["cases"], [])
        self.assertIn("credentials", result["errors"][0])
        self.assertFalse(gather_test_cases(parse_testrail_config({}, api_key=""), 1, [])["configured"])
        with self.assertRaises(TestRailError):
            TestRailClient(TESTRAIL, session=session).get("get_case/1")


class HeuristicTests(unittest.TestCase):
    def test_implementation_and_coverage(self):
        criteria = "Given a saved card, when the customer pays, then the CVV is required.\nGiven 5 saved cards, then an error asks to remove one."
        code = [{"repository": "org/shop", "number": 1, "error": None, "skipped_files": [],
                 "files": [{"filename": "checkout/cvv.py", "is_test": False, "patch": "+def require_cvv(saved_card, customer):\n+    pays = True"}]}]
        impl = heuristic_implementation(criteria, code)
        self.assertEqual([row["status"] for row in impl["criteria"]], ["partial", "unclear"])  # "saved" appears; the limit check does not
        self.assertEqual(impl["missing_tests"], ["No test files were changed in the linked pull requests."])
        tests = {"cases": [
            {"id": 11, "title": "Saved card requires CVV", "preconditions": "", "steps": [{"step": "customer pays with saved card", "expected": "CVV required"}], "attachments": [], "latest": {"status": "Passed", "run": "R", "comment": "", "attachments": []}},
            {"id": 12, "title": "Login works", "preconditions": "", "steps": [], "attachments": [], "latest": None},
        ], "errors": []}
        coverage = heuristic_test_coverage(criteria, tests)
        self.assertEqual(coverage["criteria"][0]["test_cases"], ["C11"])
        self.assertEqual(coverage["criteria"][1]["status"], "not_covered")
        self.assertIn("C12 Login works", coverage["tests_without_criteria"])
        self.assertTrue(any("passed without evidence" in issue for issue in coverage["evidence_issues"]))
        self.assertTrue(any("no result" in issue for issue in coverage["evidence_issues"]))


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _story(db):
    item = upsert_work_item(db, {"id": 1011, "project": "Shop", "work_item_type": "User Story", "title": "Saved cards", "state": "Released",
                                 "created_date": T0, "changed_date": T0, "tags": [], "description": "<p>As a customer I want saved cards so that I pay faster.</p>",
                                 "acceptance_criteria": CRITERIA})
    replace_history(db, item, [{"rev": 1, "changed_at": T0, "state": "New"}, {"rev": 2, "changed_at": T0 + timedelta(days=2), "state": "Released"}], [])
    repo, dev = Repository(name="org/shop", is_active=True), Developer(github_username="alice")
    db.add_all([repo, dev])
    db.flush()
    db.add(PullRequest(repo_id=repo.id, developer_id=dev.id, pr_number=7, title="AB#1011 CVV", created_at=T0, merged_at=T0 + timedelta(days=1), state="MERGED"))
    db.commit()


class AnalyzeWithChecksTests(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        _story(self.db)
        directory = tempfile.mkdtemp()
        Path(directory, "i.md").write_text("Rules.")
        patcher = patch.object(settings, "story_analysis_instructions", str(Path(directory, "i.md")))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.code = {"repository": "org/shop", "number": 7, "error": None, "skipped_files": [],
                     "files": [{"filename": "checkout/cvv.py", "status": "modified", "additions": 3, "deletions": 0, "is_test": False, "patch": "+def require_cvv(): ..."}]}
        self.tests = {"configured": True, "missing": [], "errors": [], "cases": [
            {"id": 11, "title": "CVV required", "url": "https://t/11", "refs": "1011", "linked_by": "References", "preconditions": "",
             "steps": [{"step": "pay", "expected": "CVV asked"}], "other_fields": {}, "attachments": [], "latest": {"status": "Passed", "run": "R1", "attachments": ["v.mp4"], "comment": "ok"}}]}

    def analyze(self, **options):
        with patch.object(story_analyzer, "fetch_pr_changes", return_value=dict(self.code)) as fetch, \
             patch.object(story_analyzer, "gather_test_cases", return_value=self.tests):
            result = asyncio.run(analyze_story(self.db, 1011, LIFECYCLE, TEAMS, devops=None, now=NOW,
                                               context_options=ContextOptions(include_parents=False, include_attachments=False, **options)))
        return result, fetch

    def test_llm_gets_code_and_tests_and_answers_are_used(self):
        seen = {}

        async def fake_chat(system, user, schema):
            seen.update(system=system, user=json.loads(user), schema=schema)
            return {"summary": "s",
                    "implementation": {"score": 50, "assessment": "CVV done, card limit missing.", "criteria": [
                        {"criterion": "CVV is required", "status": "implemented", "evidence": "checkout/cvv.py adds require_cvv"},
                        {"criterion": "Limit of 5 cards", "status": "not_found", "evidence": "no limit check"}],
                        "unrelated_changes": [], "missing_tests": ["No test for the CVV rule"]},
                    "test_coverage": {"score": 50, "assessment": "Only the CVV rule is tested.", "criteria": [
                        {"criterion": "CVV is required", "status": "covered", "test_cases": ["C11"]},
                        {"criterion": "Limit of 5 cards", "status": "not_covered", "test_cases": []}],
                        "tests_without_criteria": [], "evidence_issues": []}}, "Ollama (test)"

        with patch.object(story_analyzer, "chat_json", side_effect=fake_chat):
            result, fetch = self.analyze(include_code=True, include_tests=True)
        fetch.assert_called_once_with("org/shop", 7)
        self.assertIn("CODE CHECK", seen["system"])
        self.assertIn("TEST CHECK", seen["system"])
        self.assertEqual(seen["user"]["code_changes"][0]["files"][0]["filename"], "checkout/cvv.py")
        self.assertEqual(seen["user"]["test_cases"][0]["case"], "C11")
        self.assertIn("implementation", seen["schema"]["required"])
        self.assertIn("test_coverage", seen["schema"]["required"])
        self.assertEqual(result["implementation"]["review"]["criteria"][1]["status"], "not_found")
        self.assertEqual(result["implementation"]["pull_requests"][0]["pull_request"], "org/shop#7")
        self.assertNotIn("patch", result["implementation"]["pull_requests"][0]["files"][0])  # no diff text stored
        self.assertEqual(result["test_coverage"]["review"]["criteria"][0]["test_cases"], ["C11"])
        self.assertEqual(result["test_coverage"]["cases"][0]["latest"]["status"], "Passed")

    def test_fallbacks_options_and_cache(self):
        with patch.object(story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")):
            only_code, _ = self.analyze(include_code=True)
            self.assertIsNone(only_code["test_coverage"])
            self.assertIn("Rule-based keyword match", only_code["implementation"]["review"]["assessment"])
            again, _ = self.analyze(include_code=True)
            self.assertTrue(again["cached"])
            self.code["files"][0]["patch"] = "+def require_cvv_and_limit(): ..."
            changed, _ = self.analyze(include_code=True)
            self.assertFalse(changed["cached"])  # a new commit -> new analysis
            self.tests = {"configured": False, "missing": ["TESTRAIL_API_KEY in .env"], "errors": [], "cases": []}
            no_testrail, _ = self.analyze(include_tests=True)
        self.assertIsNone(no_testrail["test_coverage"]["review"])
        self.assertTrue(any("TestRail is not configured" in w for w in no_testrail["warnings"]))


class SplitterTests(unittest.TestCase):
    def test_heuristic_split(self):
        stories = heuristic_split("# Save card\n- card is tokenised\n- shown under payment methods\n# Delete card\n- removed at provider", 8)
        self.assertEqual([s["title"] for s in stories], ["Save card", "Delete card"])
        self.assertEqual(len(stories[0]["acceptance_criteria"]), 2)
        bullets = heuristic_split("1. Search products\n   - by name\n2. Filter by price", 1)
        self.assertEqual([s["title"] for s in bullets], ["Search products"])

    def test_llm_split_with_parent_and_documents(self):
        db = _session()
        upsert_work_item(db, {"id": 50, "project": "Shop", "work_item_type": "Feature", "title": "Saved cards", "state": "Active",
                              "created_date": T0, "changed_date": T0, "tags": [], "description": "<p>At most 5 cards per customer.</p>"})
        db.commit()
        seen = {}

        async def fake_chat(system, user, schema):
            seen.update(system=system, user=json.loads(user))
            return {"summary": "Two slices.", "open_questions": ["Which card brands?"], "assumptions": [], "stories": [
                {"title": "Save a card at checkout", "description": "As a customer, I want ...", "acceptance_criteria": ["Given ..."], "story_points": "3", "tags": ["payments"]},
                {"title": "", "description": "dropped: no title"},
                {"title": "Limit saved cards to 5", "description": "As a customer ...", "acceptance_criteria": ["Given 5 cards ..."], "story_points": None}]}, "Ollama (test)"

        with patch.object(story_splitter, "chat_json", side_effect=fake_chat):
            result = asyncio.run(split_requirement(db, "Customers save cards.", [("notes.txt", b"Cards are tokenised.")], parent_id=50, max_stories=5))
        self.assertIn("vertical slices", seen["system"])
        self.assertIn("at most 5 stories", seen["system"].lower())
        self.assertEqual(seen["user"]["parent_feature"]["description"], "At most 5 cards per customer.")
        self.assertEqual(seen["user"]["documents"][0]["text"], "Cards are tokenised.")
        self.assertEqual([s["title"] for s in result["stories"]], ["Save a card at checkout", "Limit saved cards to 5"])
        self.assertEqual(result["stories"][0]["story_points"], 3)
        self.assertEqual(result["open_questions"], ["Which card brands?"])
        self.assertEqual(result["parent"]["id"], 50)

    def test_endpoint(self):
        db = _session()
        app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(app)
            with patch("app.main.load_devops_config", return_value=DevOpsConfig()), patch.object(story_splitter, "chat_json", side_effect=LLMUnavailableError("none")):
                self.assertEqual(client.post("/api/story-analyzer/split", data={"requirement": " "}).status_code, 400)
                self.assertEqual(client.post("/api/story-analyzer/split", data={"requirement": "x", "max_stories": "99"}).status_code, 400)
                ok = client.post("/api/story-analyzer/split", data={"requirement": "- Save card\n- Delete card"}).json()
            self.assertEqual(ok["provider"], "Rule-based draft (no LLM)")
            self.assertEqual([s["title"] for s in ok["stories"]], ["Save card", "Delete card"])
            integrations = client.get("/api/story-analyzer/integrations").json()
            self.assertIn("testrail", integrations)
            self.assertNotIn("api_key", json.dumps(integrations))
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
