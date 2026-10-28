import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GITHUB_TOKEN", "test-token")

import httpx

from app import ai_analyzers
from app.ai_analyzers import (
    DeveloperAnalysisResult,
    HeuristicFallbackHandler,
    LLMAnalysis,
    LocalOllamaHandler,
    _parse_llm_json,
    get_developer_analyzer,
)

PR_DATA = {
    "metrics": {
        "requested_changes_rate": 40.0,
        "average_comments_per_pr": 2.0,
        "total_review_comments": 6,
        "pull_request_count": 3,
        "average_pr_size": 200,
    },
    "breakdown": {"comment_categories": [{"category": "testing", "count": 2}], "repeated_issue_categories": []},
}

# What the user's Ollama model returned: every field null.
ALL_NULL = json.dumps(
    {
        "coding_standards_score": None,
        "design_patterns_summary": None,
        "dry_vs_wet_observations": None,
        "reviewer_rigor_score": None,
        "actionable_feedback": None,
    }
)


def _heuristic() -> DeveloperAnalysisResult:
    return asyncio.run(HeuristicFallbackHandler().analyze_developer_metrics("dev", PR_DATA, []))


class ParseLLMJsonTests(unittest.TestCase):
    def test_all_null_response_parses_but_has_no_content(self):
        parsed = _parse_llm_json(ALL_NULL)
        self.assertFalse(parsed.has_content())

    def test_loosely_typed_values_are_normalised(self):
        parsed = _parse_llm_json(
            json.dumps(
                {
                    "coding_standards_score": "7/10",
                    "design_patterns_summary": "  Uses a repository layer.  ",
                    "dry_vs_wet_observations": "",
                    "reviewer_rigor_score": 85,
                    "actionable_feedback": "Add tests for edge cases.",
                }
            )
        )
        self.assertEqual(parsed.coding_standards_score, 7.0)
        self.assertEqual(parsed.reviewer_rigor_score, 8.5)
        self.assertEqual(parsed.design_patterns_summary, "Uses a repository layer.")
        self.assertIsNone(parsed.dry_vs_wet_observations)
        self.assertEqual(parsed.actionable_feedback, ["Add tests for edge cases."])

    def test_scores_are_clamped_and_garbage_is_dropped(self):
        parsed = LLMAnalysis.model_validate(
            {"coding_standards_score": 0, "reviewer_rigor_score": "n/a", "actionable_feedback": [None, " ", "ok", 3]}
        )
        self.assertEqual(parsed.coding_standards_score, 1.0)
        self.assertIsNone(parsed.reviewer_rigor_score)
        self.assertEqual(parsed.actionable_feedback, ["ok"])

    def test_wrapped_and_fenced_answers_are_unwrapped(self):
        raw = '```json\n{"analysis": {"coding_standards_score": 6, "actionable_feedback": ["x"]}}\n```'
        parsed = _parse_llm_json(raw)
        self.assertEqual(parsed.coding_standards_score, 6.0)
        self.assertEqual(parsed.actionable_feedback, ["x"])

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            _parse_llm_json("Sorry, I cannot help with that.")

    def test_complete_with_fills_only_missing_fields(self):
        heuristic = _heuristic()
        result, filled = LLMAnalysis(coding_standards_score=9, actionable_feedback=["Keep PRs small."]).complete_with(
            heuristic
        )
        self.assertEqual(result.coding_standards_score, 9.0)
        self.assertEqual(result.actionable_feedback, ["Keep PRs small."])
        self.assertEqual(result.design_patterns_summary, heuristic.design_patterns_summary)
        self.assertEqual(result.reviewer_rigor_score, heuristic.reviewer_rigor_score)
        self.assertEqual(filled, ["design_patterns_summary", "dry_vs_wet_observations", "reviewer_rigor_score"])


class DispatcherTests(unittest.TestCase):
    def _run_with_ollama_answer(self, raw: str) -> DeveloperAnalysisResult:
        with (
            patch.object(ai_analyzers.settings, "online_ai_enabled", False),
            patch.object(ai_analyzers, "_is_ollama_reachable", AsyncMock(return_value=True)),
            patch.object(
                LocalOllamaHandler, "analyze_developer_metrics", AsyncMock(return_value=_parse_llm_json(raw))
            ),
        ):
            return asyncio.run(get_developer_analyzer("dev", PR_DATA, []))

    def test_all_null_answer_falls_back_to_heuristic_without_error(self):
        with self.assertLogs("app.ai_analyzers", level="INFO") as logs:
            result = self._run_with_ollama_answer(ALL_NULL)
        self.assertEqual(result, _heuristic())
        self.assertEqual(result.provider, "Rule-based (heuristic)")
        output = "\n".join(logs.output)
        self.assertIn("returned no usable analysis", output)
        self.assertNotIn("validation error", output)

    def test_partial_answer_is_completed_from_heuristic(self):
        result = self._run_with_ollama_answer(
            json.dumps({"coding_standards_score": None, "design_patterns_summary": "Clear service layering."})
        )
        heuristic = _heuristic()
        self.assertEqual(result.design_patterns_summary, "Clear service layering.")
        self.assertEqual(result.coding_standards_score, heuristic.coding_standards_score)
        self.assertEqual(result.actionable_feedback, heuristic.actionable_feedback)
        self.assertEqual(result.provider, f"Ollama ({ai_analyzers.settings.ollama_model}), partly heuristic")

    def test_complete_answer_reports_ollama_provider(self):
        result = self._run_with_ollama_answer(
            json.dumps(
                {
                    "coding_standards_score": 8,
                    "design_patterns_summary": "a",
                    "dry_vs_wet_observations": "b",
                    "reviewer_rigor_score": 7,
                    "actionable_feedback": ["c"],
                }
            )
        )
        self.assertEqual(result.provider, f"Ollama ({ai_analyzers.settings.ollama_model})")

    def test_online_provider_label_names_vendor_and_model(self):
        with patch.object(ai_analyzers.settings, "online_ai_model", "gemini-1.5-pro"):
            self.assertEqual(ai_analyzers.OnlineAIHandler().provider_label, "Gemini (gemini-1.5-pro)")
        with patch.object(ai_analyzers.settings, "online_ai_model", "gpt-4o"):
            self.assertEqual(ai_analyzers.OnlineAIHandler().provider_label, "OpenAI (gpt-4o)")


class OllamaHandlerTests(unittest.TestCase):
    def test_requests_json_schema_and_retries_with_plain_json_on_400(self):
        formats: list = []
        answer = {"message": {"content": json.dumps({"coding_standards_score": 8})}}

        def respond(request: httpx.Request) -> httpx.Response:
            fmt = json.loads(request.content)["format"]
            formats.append(fmt)
            if isinstance(fmt, dict):
                return httpx.Response(400, json={"error": "invalid format"})
            return httpx.Response(200, json=answer)

        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with patch.object(
            ai_analyzers.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        ):
            result = asyncio.run(LocalOllamaHandler().analyze_developer_metrics("dev", PR_DATA, []))

        self.assertEqual(len(formats), 2)
        self.assertEqual(formats[0]["required"], list(ai_analyzers.ANALYSIS_FIELDS))
        self.assertEqual(formats[1], "json")
        self.assertEqual(result.coding_standards_score, 8.0)


if __name__ == "__main__":
    unittest.main()
