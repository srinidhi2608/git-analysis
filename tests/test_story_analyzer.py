import asyncio
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

from app import story_analyzer
from app.azure_devops import AzureDevOpsClient
from app.config import settings
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_comments, replace_history, upsert_work_item
from app.lifecycle_config import parse_lifecycle_config
from app.llm_client import LLMUnavailableError
from app.main import app
from app.models import AzureWorkItem, Base, StoryAnalysis
from app.story_analyzer import analyze_story, delivery_health, heuristic_review, html_to_text, load_instructions
from app.teams import parse_team_config

LIFECYCLE = parse_lifecycle_config({})
TEAMS = parse_team_config({})
T0 = datetime(2026, 10, 1, 9)
NOW = datetime(2026, 10, 30, 12)
GOOD_DESCRIPTION = (
    "<div>As a returning customer, I want to save my card so that I can pay faster next time.</div>"
    "<ul><li>Cards are stored with the payment provider, not in our database.</li><li>Applies to web checkout only.</li></ul>"
    "<p>Design: <a href='https://figma.example/x'>Figma</a></p>"
)
GOOD_CRITERIA = (
    "<ul><li>Given a logged-in user, when they tick 'save card' and pay, then the card is listed under Payment methods.</li>"
    "<li>Given a saved card, when the user pays, then the card is preselected and payment completes in under 3 seconds.</li>"
    "<li>Given an expired card, when the user pays, then an error is shown and no order is created.</li></ul>"
)


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _story(db, item_id=1, description=GOOD_DESCRIPTION, criteria=GOOD_CRITERIA, states=("New", "Active", "QA Dev In Progress", "Released"),
           attachments=(), comments=()):
    item = upsert_work_item(db, {"id": item_id, "project": "Shop", "work_item_type": "User Story", "title": "Save card",
                                 "state": states[-1], "created_date": T0, "changed_date": T0 + timedelta(days=len(states)),
                                 "tags": ["payments"], "description": description, "acceptance_criteria": criteria})
    history = [{"rev": i + 1, "changed_at": T0 + timedelta(days=i), "state": state} for i, state in enumerate(states)]
    relations = [{"rel": "AttachedFile", "name": name, "url": f"https://dev.azure.com/a/{name}", "target_id": None} for name in attachments]
    replace_history(db, item, history, relations)
    replace_comments(db, item, [{"comment_id": i + 1, "author": author, "created_at": T0 + timedelta(days=i), "text": text}
                                for i, (author, text) in enumerate(comments)])
    db.commit()
    return item


class ParsingTests(unittest.TestCase):
    def test_html_to_text_keeps_lists_and_counts_images(self):
        text, images, links = html_to_text("<p>Hello&nbsp;<b>world</b></p><ul><li>One</li><li>Two</li></ul><img src='a.png'><a href='https://x'>x</a>")
        self.assertEqual(text, "Hello world\n- One\n- Two[image]x")
        self.assertEqual((images, links), (1, ["https://x"]))
        self.assertEqual(html_to_text(None), ("", 0, []))

    def test_instructions_file(self):
        directory = tempfile.mkdtemp()
        path = Path(directory, "i.md")
        path.write_text("<!-- only a comment -->\n")
        self.assertFalse(load_instructions(str(path))["used"])
        path.write_text("<!-- note -->\n# Rules\n- Use Given/When/Then\n")
        info = load_instructions(str(path))
        self.assertTrue(info["used"])
        self.assertEqual(info["text"], "# Rules\n- Use Given/When/Then")
        self.assertFalse(load_instructions(str(Path(directory, "missing.md")))["exists"])


class HeuristicTests(unittest.TestCase):
    def facts(self, db, item):
        return story_analyzer.gather_facts(db, item, LIFECYCLE, TEAMS, NOW)

    def test_good_story_scores_high(self):
        db = _session()
        item = _story(db, attachments=["test-report.pdf"], comments=[("Tom", "Does this include Amex?"), ("Alice", "Yes, all cards.")])
        review = heuristic_review(self.facts(db, item), {s.name for s in LIFECYCLE.testing_stages})
        scores = {name: section["score"] for name, section in review["sections"].items()}
        self.assertGreaterEqual(scores["description"], 90)
        self.assertGreaterEqual(scores["acceptance_criteria"], 90)
        self.assertGreaterEqual(scores["evidence"], 60)
        self.assertGreaterEqual(scores["comments"], 80)
        self.assertEqual(review["comments_insights"]["unresolved_questions"], [])

    def test_empty_story_scores_low_with_suggestions(self):
        db = _session()
        item = _story(db, description=None, criteria="<p>Should work fast etc</p>", comments=[("Tom", "What about errors?")])
        review = heuristic_review(self.facts(db, item), {s.name for s in LIFECYCLE.testing_stages})
        self.assertEqual(review["sections"]["description"]["score"], 0)
        self.assertLess(review["sections"]["acceptance_criteria"]["score"], 40)
        self.assertIn("“fast”", " ".join(review["sections"]["acceptance_criteria"]["issues"]))
        self.assertEqual(len(review["comments_insights"]["unresolved_questions"]), 1)
        self.assertTrue(any(s["area"] == "Description" and s["priority"] == "high" for s in review["suggestions"]))

    def test_delivery_health_penalties(self):
        row = {"qa_bounces": 2, "reopened": 0, "skipped_stages": ["qaa"], "flags": ["open_pr_at_finish"], "flow_efficiency": 0.3}
        health = delivery_health(row)
        self.assertEqual(health["score"], 100 - 30 - 15 - 10 - 10)
        self.assertEqual(len(health["penalties"]), 4)


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.db = _session()
        _story(self.db, attachments=["screenshot.png"])
        self.instructions = Path(tempfile.mkdtemp(), "story.md")
        self.instructions.write_text("Always require a rollback plan.")
        patcher = patch.object(settings, "story_analysis_instructions", str(self.instructions))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_analysis(self, **kw):
        return asyncio.run(analyze_story(self.db, 1, LIFECYCLE, TEAMS, devops=None, now=NOW, **kw))

    def test_without_llm_uses_rule_based_and_caches(self):
        with patch.object(story_analyzer, "chat_json", side_effect=LLMUnavailableError("no LLM configured")) as chat:
            first = self.run_analysis()
            second = self.run_analysis()
        self.assertEqual(first["provider"], "Rule-based (no LLM)")
        self.assertIn("rule-based review", first["warnings"][0])
        self.assertEqual(first["health"]["level"], "Healthy")
        self.assertEqual(first["health"]["score"], round(0.6 * first["health"]["content_score"] + 0.4 * first["health"]["delivery_score"]))
        self.assertEqual([seg["state"] for seg in first["lifecycle"]["timeline"]], ["New", "Active", "QA Dev In Progress", "Released"])
        self.assertTrue(second["cached"])
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(self.db.query(StoryAnalysis).count(), 1)
        # Changing the instructions invalidates the cache.
        self.instructions.write_text("Require a rollback plan and a feature flag.")
        with patch.object(story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")):
            self.assertFalse(self.run_analysis()["cached"])

    def test_llm_answer_is_used_and_gaps_are_filled(self):
        answer = {
            "summary": "Good story, but no rollback plan.",
            "description": {"score": 70, "assessment": "Clear goal; rollback plan missing.", "strengths": ["User story form"], "issues": ["No rollback plan"]},
            "acceptance_criteria": {"score": "88", "assessment": "Testable.", "strengths": [], "issues": []},
            "evidence": {"score": None, "assessment": ""},  # unusable -> rule-based
            "comments_insights": {"key_points": ["Scope agreed"], "unresolved_questions": []},
            "suggestions": [{"area": "Description", "priority": "high", "change": "Add a rollback plan.", "example": "Disable the flag."}],
            "suggested_acceptance_criteria": ["Given ..., when ..., then ..."],
            "suggested_tags": ["payments"],
        }

        async def fake_chat(system, user, schema):
            self.assertIn("Always require a rollback plan.", system)
            self.assertIn("Save card", user)
            return answer, "Ollama (test)"

        with patch.object(story_analyzer, "chat_json", side_effect=fake_chat):
            result = self.run_analysis(use_cache=False)
        self.assertEqual(result["provider"], "Ollama (test) + rule-based for evidence, comments")
        self.assertEqual(result["sections"]["description"]["score"], 70)
        self.assertEqual(result["sections"]["acceptance_criteria"]["score"], 88)
        self.assertEqual(result["health"]["summary"], "Good story, but no rollback plan.")
        self.assertEqual(result["suggestions"][0]["change"], "Add a rollback plan.")
        self.assertEqual(result["suggested_acceptance_criteria"], ["Given ..., when ..., then ..."])
        self.assertTrue(result["instructions"]["used"])

    def test_refresh_from_azure_stores_fields_links_and_comments(self):
        client = MagicMock()
        client.fetch_work_item.return_value = {
            "item": {"id": 1, "project": "Shop", "work_item_type": "User Story", "title": "Save card v2", "state": "Released",
                     "created_date": T0, "changed_date": NOW, "tags": ["payments"], "description": GOOD_DESCRIPTION, "acceptance_criteria": GOOD_CRITERIA},
            "relations": [{"rel": "AttachedFile", "name": "evidence.mp4", "url": "https://x/evidence.mp4", "target_id": None}],
        }
        client.fetch_comments.return_value = [{"comment_id": 9, "author": "Tom", "created_at": "2026-10-20T10:00:00Z", "text": "<p>Verified in QAA</p>"}]
        devops = DevOpsConfig(organization="corp", projects=["Shop"], pat="x")
        with patch("app.azure_devops.AzureDevOpsClient", return_value=client), patch.object(
            story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")
        ):
            result = asyncio.run(analyze_story(self.db, 1, LIFECYCLE, TEAMS, devops=devops, refresh=True, now=NOW))
        self.assertEqual(result["story"]["title"], "Save card v2")
        self.assertEqual([a["name"] for a in result["inputs"]["attachments"]], ["evidence.mp4"])
        self.assertEqual(result["inputs"]["comments"][0]["text"], "Verified in QAA")
        item = self.db.get(AzureWorkItem, 1)
        self.assertEqual(len(item.history), 4)  # state history kept

    def test_api(self):
        app.dependency_overrides[get_db] = lambda: self.db
        try:
            client = TestClient(app)
            stories = client.get("/api/story-analyzer/stories?search=save").json()["stories"]
            self.assertEqual([s["id"] for s in stories], [1])
            with patch("app.main.load_devops_config", return_value=DevOpsConfig()):
                missing = client.post("/api/story-analyzer/analyze", json={"work_item_id": 999})
                self.assertEqual(missing.status_code, 404)
                self.assertIn("not configured", missing.json()["detail"])
                with patch.object(story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")):
                    ok = client.post("/api/story-analyzer/analyze", json={"work_item_id": 1})
            self.assertEqual(ok.status_code, 200)
            self.assertIn(ok.json()["health"]["level"], ("Healthy", "Needs attention", "At risk"))
            self.assertTrue(client.get("/api/story-analyzer/instructions").json()["used"])
        finally:
            app.dependency_overrides.clear()


class ClientTests(unittest.TestCase):
    def test_comments_use_the_preview_api_and_skip_deleted(self):
        session = MagicMock()
        session.headers = {}
        response = MagicMock(status_code=200, ok=True, headers={"Content-Type": "application/json"})
        response.json.return_value = {"comments": [
            {"id": 1, "text": "<p>Hi</p>", "createdBy": {"displayName": "Tom", "uniqueName": "tom@x.com"}, "createdDate": "2026-10-01T10:00:00Z"},
            {"id": 2, "text": "gone", "isDeleted": True},
        ]}
        session.request.return_value = response
        comments = AzureDevOpsClient(DevOpsConfig(organization="corp", projects=["Shop"], pat="x"), session=session).fetch_comments("Shop", 5)
        self.assertEqual([(c["comment_id"], c["author"], c["author_email"]) for c in comments], [(1, "Tom", "tom@x.com")])
        self.assertEqual(session.request.call_args.kwargs["params"]["api-version"], "7.1-preview.4")


if __name__ == "__main__":
    unittest.main()
