"""
3-tier fallback architecture for developer metrics analysis:
  1. OnlineAIHandler  – remote LLM API (e.g. Gemini, OpenAI)
  2. LocalOllamaHandler – local Ollama model
  3. HeuristicFallbackHandler – deterministic rule-based engine
"""
from __future__ import annotations

import abc
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class DeveloperAnalysisResult(BaseModel):
    coding_standards_score: float
    design_patterns_summary: str
    dry_vs_wet_observations: str
    reviewer_rigor_score: float
    actionable_feedback: list[str]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseMetricsAnalyzer(abc.ABC):
    @abc.abstractmethod
    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult:
        """Analyze developer metrics and return structured insights."""


# ---------------------------------------------------------------------------
# Shared prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior engineering analytics assistant that objectively reviews developer pull-request metrics.

Analyze the provided PR descriptions and review comments specifically for:
1. Adherence to clean coding practices and language/framework idioms (score 1-10).
2. Design patterns used or missing — identify architectural intent.
3. DRY vs WET balance — flag copy-paste logic or over-abstraction.
4. Reviewer engagement quality — depth of comments left on others' PRs (score 1-10).

Return ONLY valid JSON matching this exact schema (no prose, no markdown fences):
{
  "coding_standards_score": <float 1-10>,
  "design_patterns_summary": "<string>",
  "dry_vs_wet_observations": "<string>",
  "reviewer_rigor_score": <float 1-10>,
  "actionable_feedback": ["<string>", ...]
}
"""


def _build_user_message(
    developer: str,
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "developer": developer,
            "pr_metrics": pr_data,
            "review_comments_sample": review_comments[:20],
        },
        default=str,
    )


def _parse_llm_json(raw: str) -> DeveloperAnalysisResult:
    """Extract JSON from a raw LLM response and parse it."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return DeveloperAnalysisResult.model_validate(json.loads(raw))


# ---------------------------------------------------------------------------
# Handler: Online AI (OpenAI-compatible chat API)
# ---------------------------------------------------------------------------


class OnlineAIHandler(BaseMetricsAnalyzer):
    """Calls a remote OpenAI-compatible chat API (e.g. Gemini via openai SDK shim,
    OpenAI, Azure OpenAI, etc.)."""

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult:
        if not settings.online_ai_api_key:
            raise RuntimeError("ONLINE_AI_API_KEY is not set")

        # Detect provider by model name prefix and set base URL accordingly
        model = settings.online_ai_model
        if model.startswith("gemini"):
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        else:
            base_url = "https://api.openai.com/v1"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_message(developer, pr_data, review_comments),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": "Bearer " + (settings.online_ai_api_key or ""),
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()

        content = (
            response.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return _parse_llm_json(content)


# ---------------------------------------------------------------------------
# Handler: Local Ollama
# ---------------------------------------------------------------------------


class LocalOllamaHandler(BaseMetricsAnalyzer):
    """Sends requests to a local Ollama instance at OLLAMA_BASE_URL."""

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult:
        url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        payload = {
            "model": settings.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_message(developer, pr_data, review_comments),
                },
            ],
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")
        return _parse_llm_json(content)


# ---------------------------------------------------------------------------
# Handler: Heuristic fallback
# ---------------------------------------------------------------------------


class HeuristicFallbackHandler(BaseMetricsAnalyzer):
    """Deterministic rule-based analyzer that never fails."""

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult:
        metrics = pr_data.get("metrics", {})
        breakdown = pr_data.get("breakdown", {})

        # Coding standards score (1-10)
        requested_changes_rate = metrics.get("requested_changes_rate", 0)
        comments_per_pr = metrics.get("average_comments_per_pr", 0)
        correctness_security_count = sum(
            item["count"]
            for item in breakdown.get("comment_categories", [])
            if item["category"] in {"correctness", "security"}
        )
        categorized_total = sum(
            item["count"] for item in breakdown.get("comment_categories", [])
        )
        raw_coding = (
            9.2
            - min(comments_per_pr * 0.7, 2.5)
            - min(requested_changes_rate * 0.045, 2.0)
            - (
                0
                if categorized_total == 0
                else (correctness_security_count / categorized_total) * 1.8
            )
        )
        coding_standards_score = round(max(1.0, min(10.0, raw_coding)), 1)

        # Design patterns summary
        repeated = [
            item["category"]
            for item in breakdown.get("repeated_issue_categories", [])[:3]
        ]
        if repeated:
            design_patterns_summary = (
                f"Recurring review themes ({', '.join(repeated)}) suggest areas where "
                "established patterns or abstractions could reduce churn."
            )
        else:
            design_patterns_summary = (
                "No strong recurring pattern signals detected from saved review data."
            )

        # DRY vs WET
        maintainability_count = next(
            (
                item["count"]
                for item in breakdown.get("comment_categories", [])
                if item["category"] == "maintainability"
            ),
            0,
        )
        if maintainability_count > 3:
            dry_vs_wet = (
                f"{maintainability_count} maintainability comments suggest possible "
                "code duplication or over-abstraction worth reviewing."
            )
        else:
            dry_vs_wet = "No clear DRY/WET issues flagged in saved review feedback."

        # Reviewer rigor score: based on comment depth across PRs
        total_comments = metrics.get("total_review_comments", 0)
        pr_count = metrics.get("pull_request_count", 1) or 1
        avg_comments = total_comments / pr_count
        raw_rigor = min(10.0, 3.0 + avg_comments * 1.5)
        reviewer_rigor_score = round(max(1.0, raw_rigor), 1)

        # Actionable feedback
        feedback: list[str] = []
        if requested_changes_rate > 30:
            feedback.append(
                f"Reduce requested-changes rate (currently {requested_changes_rate:.1f}%) "
                "by strengthening pre-review self-review."
            )
        if metrics.get("average_pr_size", 0) > 600:
            feedback.append(
                f"Split large PRs (avg {metrics['average_pr_size']:.0f} lines) into "
                "smaller, focused changes to ease reviewer load."
            )
        if maintainability_count > 3:
            feedback.append(
                "Address maintainability feedback by extracting shared logic into "
                "well-named helpers or services."
            )
        if avg_comments > 5:
            feedback.append(
                "High comment density may indicate unclear code intent — add inline "
                "documentation for complex sections."
            )
        if not feedback:
            feedback.append(
                "Metrics look healthy. Continue applying current practices and "
                "iterate on reviewer feedback promptly."
            )

        return DeveloperAnalysisResult(
            coding_standards_score=coding_standards_score,
            design_patterns_summary=design_patterns_summary,
            dry_vs_wet_observations=dry_vs_wet,
            reviewer_rigor_score=reviewer_rigor_score,
            actionable_feedback=feedback,
        )


# ---------------------------------------------------------------------------
# Dispatcher factory
# ---------------------------------------------------------------------------


async def _is_ollama_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.ollama_base_url.rstrip('/')}/api/tags"
            )
            return response.status_code == 200
    except Exception:
        return False


async def get_developer_analyzer(
    developer: str,
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
) -> DeveloperAnalysisResult:
    """
    3-tier fallback dispatcher:
      1. OnlineAIHandler  (if ONLINE_AI_ENABLED=True and API key present)
      2. LocalOllamaHandler (if Ollama is reachable)
      3. HeuristicFallbackHandler (always succeeds)
    """
    # Tier 1: Online AI
    if settings.online_ai_enabled and settings.online_ai_api_key:
        try:
            handler: BaseMetricsAnalyzer = OnlineAIHandler()
            result = await handler.analyze_developer_metrics(developer, pr_data, review_comments)
            logger.info("Developer metrics analyzed via OnlineAIHandler for %s.", developer)
            return result
        except Exception as exc:
            logger.warning(
                "OnlineAIHandler failed for %s (%s). Falling back to LocalOllamaHandler.",
                developer,
                exc,
            )

    # Tier 2: Local Ollama
    if await _is_ollama_reachable():
        try:
            handler = LocalOllamaHandler()
            result = await handler.analyze_developer_metrics(developer, pr_data, review_comments)
            logger.info("Developer metrics analyzed via LocalOllamaHandler for %s.", developer)
            return result
        except Exception as exc:
            logger.warning(
                "LocalOllamaHandler failed for %s (%s). Falling back to HeuristicFallbackHandler.",
                developer,
                exc,
            )
    else:
        logger.warning(
            "Ollama is not reachable at %s. Falling back to HeuristicFallbackHandler.",
            settings.ollama_base_url,
        )

    # Tier 3: Heuristic fallback (always succeeds)
    handler = HeuristicFallbackHandler()
    result = await handler.analyze_developer_metrics(developer, pr_data, review_comments)
    logger.info("Developer metrics analyzed via HeuristicFallbackHandler for %s.", developer)
    return result
