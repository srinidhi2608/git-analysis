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
import re
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


HEURISTIC_PROVIDER = "Rule-based (heuristic)"


class DeveloperAnalysisResult(BaseModel):
    coding_standards_score: float
    design_patterns_summary: str
    dry_vs_wet_observations: str
    reviewer_rigor_score: float
    actionable_feedback: list[str]
    # Which analyzer produced the result, shown as the dashboard's "Provider" tag.
    provider: str = HEURISTIC_PROVIDER


ANALYSIS_FIELDS = tuple(name for name in DeveloperAnalysisResult.model_fields if name != "provider")
_NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


class LLMAnalysis(BaseModel):
    """Lenient view of an LLM answer: any field may be missing, null or loosely typed.

    LLMs (local models in particular) sometimes return ``null`` for fields they
    have little evidence for, numbers as strings ("7/10") or a single string
    instead of a list. Unusable values become ``None``; the dispatcher fills
    them from the heuristic analysis instead of discarding the whole answer.
    """

    coding_standards_score: float | None = None
    design_patterns_summary: str | None = None
    dry_vs_wet_observations: str | None = None
    reviewer_rigor_score: float | None = None
    actionable_feedback: list[str] = []

    @field_validator("coding_standards_score", "reviewer_rigor_score", mode="before")
    @classmethod
    def _parse_score(cls, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            match = _NUMBER_PATTERN.search(value)
            if match is None:
                return None
            number = float(match.group())
        else:
            return None
        if 10 < number <= 100:  # answered on a 0-100 scale
            number /= 10
        return round(min(10.0, max(1.0, number)), 1)

    @field_validator("design_patterns_summary", "dry_vs_wet_observations", mode="before")
    @classmethod
    def _parse_text(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @field_validator("actionable_feedback", mode="before")
    @classmethod
    def _parse_feedback(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def has_content(self) -> bool:
        return any(
            [
                self.coding_standards_score is not None,
                self.design_patterns_summary,
                self.dry_vs_wet_observations,
                self.reviewer_rigor_score is not None,
                self.actionable_feedback,
            ]
        )

    def complete_with(
        self, fallback: DeveloperAnalysisResult, provider: str = ""
    ) -> tuple[DeveloperAnalysisResult, list[str]]:
        """Fill missing fields from ``fallback``; returns the result and the filled field names."""
        values: dict[str, Any] = {}
        filled: list[str] = []
        for name in ANALYSIS_FIELDS:
            value = getattr(self, name)
            if value is None or value == []:
                value = getattr(fallback, name)
                filled.append(name)
            values[name] = value
        if provider:
            values["provider"] = f"{provider}, partly heuristic" if filled else provider
        return DeveloperAnalysisResult(**values), filled


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseMetricsAnalyzer(abc.ABC):
    @property
    def provider_label(self) -> str:
        """Human-readable provider name shown in the dashboard."""
        return type(self).__name__

    @abc.abstractmethod
    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult | LLMAnalysis:
        """Analyze developer metrics and return structured insights.

        LLM-backed handlers return a possibly incomplete :class:`LLMAnalysis`;
        the dispatcher completes it from the heuristic analysis.
        """


# ---------------------------------------------------------------------------
# Shared prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior engineering analytics assistant that objectively reviews developer pull-request metrics.

You receive aggregate PR metrics for one developer plus a sample of review comments that
reviewers left on that developer's pull requests (with PR title and file path).

Using ONLY that evidence, assess:
1. Adherence to clean coding practices and language/framework idioms (score 1-10).
2. Design patterns used or missing — identify architectural intent.
3. DRY vs WET balance — flag copy-paste logic or over-abstraction.
4. Review rigor — how thorough and substantive the review feedback on these PRs is (score 1-10).

Every field is required and must never be null. Scores are numbers from 1 to 10. If the
comment sample is empty or too small for a confident judgement, still give your best estimate
from the PR metrics and say explicitly in the text fields that the evidence is limited.

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
            "review_comments_sample": review_comments[:30],
        },
        default=str,
    )


# JSON schema for Ollama structured outputs (`format`), mirroring _SYSTEM_PROMPT.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "coding_standards_score": {"type": "number", "minimum": 1, "maximum": 10},
        "design_patterns_summary": {"type": "string"},
        "dry_vs_wet_observations": {"type": "string"},
        "reviewer_rigor_score": {"type": "number", "minimum": 1, "maximum": 10},
        "actionable_feedback": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(ANALYSIS_FIELDS),
}


def _parse_llm_json(raw: str) -> LLMAnalysis:
    """Extract JSON from a raw LLM response and parse it leniently."""
    text = (raw or "").strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.debug("Unparseable LLM response: %.500s", raw)
        raise ValueError(f"LLM response is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"LLM response is a JSON {type(data).__name__}, expected an object")
    # Some models wrap the answer, e.g. {"analysis": {...}}.
    if not any(key in data for key in ANALYSIS_FIELDS):
        nested = [value for value in data.values() if isinstance(value, dict)]
        if len(nested) == 1:
            data = nested[0]
    return LLMAnalysis.model_validate(data)


# ---------------------------------------------------------------------------
# Handler: Online AI (OpenAI-compatible chat API)
# ---------------------------------------------------------------------------


class OnlineAIHandler(BaseMetricsAnalyzer):
    """Calls a remote OpenAI-compatible chat API (e.g. Gemini via openai SDK shim,
    OpenAI, Azure OpenAI, etc.)."""

    @property
    def provider_label(self) -> str:
        model = settings.online_ai_model
        vendor = "Gemini" if model.startswith("gemini") else "OpenAI"
        return f"{vendor} ({model})"

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> LLMAnalysis:
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
        logger.info("Sending request to online AI at %s", base_url)
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

    @property
    def provider_label(self) -> str:
        return f"Ollama ({settings.ollama_model})"

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> LLMAnalysis:
        url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_message(developer, pr_data, review_comments),
                },
            ],
            # Structured outputs: constrain the reply to the schema (no nulls).
            "format": _RESPONSE_SCHEMA,
        }
        logger.info("Sending request to local Ollama instance at %s", url)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 400:
                # Ollama older than 0.5 only accepts format="json".
                logger.info("Ollama rejected the JSON schema format; retrying with format=json.")
                payload["format"] = "json"
                response = await client.post(url, json=payload)
            response.raise_for_status()

        content = response.json().get("message", {}).get("content", "")
        return _parse_llm_json(content)


# ---------------------------------------------------------------------------
# Handler: Heuristic fallback
# ---------------------------------------------------------------------------


class HeuristicFallbackHandler(BaseMetricsAnalyzer):
    """Deterministic rule-based analyzer that never fails."""

    @property
    def provider_label(self) -> str:
        return HEURISTIC_PROVIDER

    async def analyze_developer_metrics(
        self,
        developer: str,
        pr_data: dict[str, Any],
        review_comments: list[dict[str, Any]],
    ) -> DeveloperAnalysisResult:
        metrics = pr_data.get("metrics", {})
        breakdown = pr_data.get("breakdown", {})
        
        logger.info("Analyzing developer metrics for %s", developer)
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
        if requested_changes_rate > settings.requested_changes_risky_pct:
            feedback.append(
                f"Reduce requested-changes rate (currently {requested_changes_rate:.1f}%) "
                "by strengthening pre-review self-review."
            )
        if metrics.get("average_pr_size", 0) > settings.large_pr_threshold_lines:
            feedback.append(
                f"Split large PRs (avg {metrics['average_pr_size']:.0f} lines) into "
                "smaller, focused changes to ease reviewer load."
            )
        if maintainability_count > 3:
            feedback.append(
                "Address maintainability feedback by extracting shared logic into "
                "well-named helpers or services."
            )
        if avg_comments > settings.high_comments_per_pr_threshold * 2.5:
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


async def _run_llm_tier(
    handler: BaseMetricsAnalyzer,
    developer: str,
    pr_data: dict[str, Any],
    review_comments: list[dict[str, Any]],
) -> DeveloperAnalysisResult | None:
    """Run an LLM handler; returns ``None`` when it fails or gives nothing usable."""
    name = type(handler).__name__
    try:
        llm_result = await handler.analyze_developer_metrics(developer, pr_data, review_comments)
    except Exception as exc:
        logger.warning("%s failed for %s: %s", name, developer, exc)
        return None
    if not isinstance(llm_result, LLMAnalysis) or not llm_result.has_content():
        logger.warning("%s returned no usable analysis for %s.", name, developer)
        return None

    heuristic = await HeuristicFallbackHandler().analyze_developer_metrics(developer, pr_data, review_comments)
    result, filled = llm_result.complete_with(heuristic, provider=handler.provider_label)
    if filled:
        logger.info(
            "%s left %s empty for %s; filled from the heuristic analysis.", name, ", ".join(filled), developer
        )
    logger.info("Developer metrics analyzed via %s for %s.", name, developer)
    return result


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

    A tier whose answer is partly missing is completed from the heuristic
    analysis; a tier that fails or answers with nothing usable falls through.
    """
    # Tier 1: Online AI
    if settings.online_ai_enabled and settings.online_ai_api_key:
        result = await _run_llm_tier(OnlineAIHandler(), developer, pr_data, review_comments)
        if result is not None:
            return result

    # Tier 2: Local Ollama
    if await _is_ollama_reachable():
        result = await _run_llm_tier(LocalOllamaHandler(), developer, pr_data, review_comments)
        if result is not None:
            return result
    else:
        logger.warning("Ollama is not reachable at %s.", settings.ollama_base_url)

    # Tier 3: Heuristic fallback (always succeeds)
    result = await HeuristicFallbackHandler().analyze_developer_metrics(developer, pr_data, review_comments)
    logger.info("Developer metrics analyzed via HeuristicFallbackHandler for %s.", developer)
    return result
