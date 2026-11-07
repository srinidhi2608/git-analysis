import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import story_analyzer
from app.azure_devops import AzureDevOpsClient, AzureDevOpsError
from app.config import settings
from app.database import get_db
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_history, upsert_work_item
from app.document_text import MAX_DOCUMENT_BYTES, extract_text
from app.lifecycle_config import parse_lifecycle_config
from app.llm_client import LLMUnavailableError
from app.main import app
from app.models import AzureWorkItem, Base
from app.story_analyzer import analyze_story, html_to_text
from app.story_context import ContextOptions, context_prompt_payload, gather_context
from app.teams import parse_team_config

LIFECYCLE = parse_lifecycle_config({})
TEAMS = parse_team_config({})
T0 = datetime(2026, 10, 1, 9)
NOW = datetime(2026, 10, 30, 12)
DEVOPS = DevOpsConfig(organization="corp", projects=["Shop"], pat="x")


def make_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def make_docx(*paragraphs: str, table: list[list[str]] | None = None) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _item(db, item_id, item_type, title, *, parent=None, description=None, criteria=None, attachments=()):
    item = upsert_work_item(db, {"id": item_id, "project": "Shop", "work_item_type": item_type, "title": title, "state": "Active",
                                 "created_date": T0, "changed_date": T0, "tags": [], "parent_id": parent,
                                 "description": description, "acceptance_criteria": criteria})
    replace_history(db, item, [{"rev": 1, "changed_at": T0, "state": "New"}, {"rev": 2, "changed_at": T0 + timedelta(days=1), "state": "Active"}],
                    [{"rel": "AttachedFile", "name": name, "url": f"https://dev.azure.com/corp/_apis/wit/attachments/{name}", "target_id": None} for name in attachments])
    return item


class DocumentTextTests(unittest.TestCase):
    def test_word_pdf_and_text(self):
        word = extract_text("spec.docx", make_docx("Refunds are allowed within 14 days.", table=[["Field", "Rule"], ["Amount", "> 0"]]))
        self.assertTrue(word.used)
        self.assertIn("Refunds are allowed within 14 days.", word.text)
        self.assertIn("Amount | > 0", word.text)
        self.assertEqual(extract_text("spec.PDF", make_pdf("Refunds within 14 days")).text, "Refunds within 14 days")
        self.assertEqual(extract_text("notes.txt", "Café rule".encode("utf-8")).text, "Café rule")
        self.assertEqual(extract_text("notes.md", "UTF-16 text".encode("utf-16")).text, "UTF-16 text")

    def test_skipped_files_explain_why(self):
        self.assertIn(".docx", extract_text("old.doc", b"x").skipped)
        self.assertIn("not read", extract_text("screen.png", b"x").skipped)
        self.assertIn("could not be read", extract_text("broken.pdf", b"not a pdf").skipped)
        self.assertIn("no text", extract_text("empty.txt", b"  ").skipped)
        self.assertIn("larger than", extract_text("big.txt", b"x" * (MAX_DOCUMENT_BYTES + 1)).skipped)


class GatherContextTests(unittest.TestCase):
    def setUp(self):
        self.db = db = _session()
        _item(db, 1, "Epic", "Payments 2027", description="<p>Customers can pay and get refunds.</p>")
        _item(db, 10, "Feature", "Refunds", parent=1, description="<p>Refunds must be possible within 14 days of purchase.</p>",
              criteria="<p>Partial refunds are supported.</p>", attachments=["refund-policy.pdf", "mockup.png"])
        _item(db, 100, "User Story", "Refund button", parent=10, description="<p>As a customer I want a refund button.</p>",
              attachments=["notes.txt", "legacy.doc"])
        db.commit()

    def test_parents_attachments_and_uploads(self):
        client = MagicMock()
        files = {"refund-policy.pdf": make_pdf("Refunds over 500 EUR need approval"), "notes.txt": b"Support team wants an audit log."}
        client.download_attachment.side_effect = lambda url, limit: files[url.rsplit("/", 1)[-1]]
        client.fetch_work_item.side_effect = AzureDevOpsError("offline")  # parents come from the database
        options = ContextOptions(uploads=[("requirements.docx", make_docx("Refund reasons are mandatory."))])
        with patch("app.azure_devops.AzureDevOpsClient", return_value=client):
            context = asyncio.run(gather_context(self.db, self.db.get(AzureWorkItem, 100), DEVOPS, options, html_to_text))
        self.assertEqual([(p["type"], p["id"]) for p in context["parents"]], [("Feature", 10), ("Epic", 1)])
        self.assertEqual(context["parents"][0]["attachments"], ["refund-policy.pdf", "mockup.png"])
        documents = {d["name"]: d for d in context["documents"]}
        self.assertTrue(documents["notes.txt"]["used"])
        self.assertEqual(documents["notes.txt"]["source"], "User Story #100")
        self.assertIn(".docx", documents["legacy.doc"]["skipped"])
        self.assertTrue(documents["refund-policy.pdf"]["used"])
        self.assertEqual(documents["refund-policy.pdf"]["source"], "Feature #10")
        self.assertIn("not read", documents["mockup.png"]["skipped"])
        self.assertTrue(documents["requirements.docx"]["used"])
        self.assertEqual(documents["requirements.docx"]["source"], "uploaded")
        self.assertEqual(len(context["warnings"]), 2)  # both parents could not be refreshed

        payload = context_prompt_payload(context)
        self.assertEqual({d["name"] for d in payload["documents"]}, {"notes.txt", "refund-policy.pdf", "requirements.docx"})
        self.assertEqual({d["name"] for d in payload["documents_not_read"]}, {"legacy.doc", "mockup.png"})

    def test_options_and_no_azure(self):
        story = self.db.get(AzureWorkItem, 100)
        context = asyncio.run(gather_context(self.db, story, None, ContextOptions(include_parents=False), html_to_text))
        self.assertEqual(context["parents"], [])
        self.assertIn("not configured", {d["name"]: d for d in context["documents"]}["notes.txt"]["skipped"])
        context = asyncio.run(gather_context(self.db, story, None, ContextOptions(include_attachments=False), html_to_text))
        self.assertEqual(len(context["parents"]), 2)
        self.assertEqual(context["documents"], [])


class DeepAnalysisTests(unittest.TestCase):
    def setUp(self):
        GatherContextTests.setUp(self)
        instructions = Path(tempfile.mkdtemp(), "i.md")
        instructions.write_text("Check refunds carefully.")
        patcher = patch.object(settings, "story_analysis_instructions", str(instructions))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_analysis(self, uploads=(), **kw):
        options = ContextOptions(include_attachments=False, uploads=list(uploads))
        return asyncio.run(analyze_story(self.db, 100, LIFECYCLE, TEAMS, devops=None, now=NOW, context_options=options, **kw))

    def test_llm_gets_context_and_reviews_it(self):
        seen = {}

        async def fake_chat(system, user, schema):
            seen.update(system=system, user=user, schema=schema)
            return {"summary": "s", "context": {"score": 40, "assessment": "Misses the 14-day rule.", "gaps": ["14-day limit"],
                                               "conflicts": [], "sources_used": ["Feature #10", "spec.docx"]}}, "Ollama (test)"

        with patch.object(story_analyzer, "chat_json", side_effect=fake_chat):
            result = self.run_analysis(uploads=[("spec.docx", make_docx("Refund reasons are mandatory."))], use_cache=False)
        self.assertIn("DEEP ANALYSIS", seen["system"])
        self.assertIn("Refunds must be possible within 14 days", seen["user"])
        self.assertIn("Refund reasons are mandatory.", seen["user"])
        self.assertIn("context", seen["schema"]["required"])
        self.assertEqual(result["context"]["review"]["score"], 40)
        self.assertEqual(result["context"]["review"]["gaps"], ["14-day limit"])
        self.assertEqual([p["id"] for p in result["context"]["parents"]], [10, 1])
        self.assertEqual([d["name"] for d in result["context"]["documents"]], ["spec.docx"])
        self.assertNotIn("text", result["context"]["documents"][0])  # document text is not stored

    def test_without_llm_and_cache_keys(self):
        with patch.object(story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")) as chat:
            first = self.run_analysis()
            again = self.run_analysis()
            other_upload = self.run_analysis(uploads=[("a.txt", b"new requirement text about refunds")])
            basic = asyncio.run(analyze_story(self.db, 100, LIFECYCLE, TEAMS, devops=None, now=NOW))
        review = first["context"]["review"]
        self.assertIsNotNone(review["score"])
        self.assertTrue(any("“refunds”" in gap or "“purchase”" in gap for gap in review["gaps"]))
        self.assertTrue(again["cached"])
        self.assertFalse(other_upload["cached"])  # a different document -> new analysis
        self.assertIsNone(basic["context"])  # the normal analysis is cached separately
        self.assertEqual(chat.call_count, 3)


class ApiAndClientTests(unittest.TestCase):
    def test_deep_endpoint_validates_uploads(self):
        db = _session()
        _item(db, 100, "User Story", "Refund button", description="<p>As a customer I want a refund button.</p>")
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(app)
            with patch("app.main.load_devops_config", return_value=DevOpsConfig()), patch.object(
                story_analyzer, "chat_json", side_effect=LLMUnavailableError("x")
            ):
                ok = client.post("/api/story-analyzer/analyze/deep", data={"work_item_id": "100", "include_attachments": "false"},
                                 files=[("files", ("spec.docx", make_docx("Refunds within 14 days."), "application/octet-stream"))])
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(ok.json()["context"]["documents"][0]["source"], "uploaded")
                bad = client.post("/api/story-analyzer/analyze/deep", data={"work_item_id": "100"}, files=[("files", ("pic.png", b"x", "image/png"))])
                self.assertEqual(bad.status_code, 400)
                self.assertIn("Word (.docx), PDF and text", bad.json()["detail"])
                many = client.post("/api/story-analyzer/analyze/deep", data={"work_item_id": "100"},
                                   files=[("files", (f"{i}.txt", b"x", "text/plain")) for i in range(6)])
                self.assertEqual(many.status_code, 400)
        finally:
            app.dependency_overrides.clear()

    def test_download_attachment_guards(self):
        session = MagicMock()
        session.headers = {}
        response = MagicMock(status_code=200, headers={"Content-Type": "application/octet-stream", "Content-Length": "5"})
        response.iter_content.return_value = [b"hello"]
        session.get.return_value.__enter__.return_value = response
        client = AzureDevOpsClient(DEVOPS, session=session)
        self.assertEqual(client.download_attachment("https://x/att", 100), b"hello")
        self.assertEqual(session.get.call_args.kwargs["params"]["download"], "true")
        response.headers = {"Content-Type": "text/html"}
        with self.assertRaises(AzureDevOpsError):
            client.download_attachment("https://x/att", 100)
        response.headers = {"Content-Type": "application/pdf", "Content-Length": "500"}
        with self.assertRaises(ValueError):
            client.download_attachment("https://x/att", 100)


if __name__ == "__main__":
    unittest.main()
