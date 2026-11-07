"""Generic JSON chat with the configured LLMs, in the same order as the developer analytics.

1. Online OpenAI-compatible API (``ONLINE_AI_ENABLED`` + ``ONLINE_AI_API_KEY``;
   ``gemini-*`` models go to Google, others to OpenAI)
2. Local Ollama (``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL``), when reachable

``chat_json`` returns the parsed JSON object and the provider label, or raises
``LLMUnavailableError`` when no tier answered with usable JSON. Callers keep a
rule-based fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ONLINE_TIMEOUT_SECONDS = 90
OLLAMA_TIMEOUT_SECONDS = 180
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMUnavailableError(RuntimeError):
    """No LLM is configured or reachable, or none returned usable JSON."""


def _parse(content: str) -> dict[str, Any]:
    text = _FENCE.sub("", (content or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def online_label() -> str:
    model = settings.online_ai_model
    return f"{'Gemini' if model.startswith('gemini') else 'OpenAI'} ({model})"


async def _online(system: str, user: str) -> dict[str, Any]:
    model = settings.online_ai_model
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai" if model.startswith("gemini") else "https://api.openai.com/v1"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=ONLINE_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": "Bearer " + (settings.online_ai_api_key or ""), "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    return _parse(response.json().get("choices", [{}])[0].get("message", {}).get("content", ""))


async def _ollama_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            return (await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")).status_code == 200
    except Exception:
        return False


async def _ollama(system: str, user: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.ollama_model,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema or "json",
        "options": {"temperature": 0.2},
    }
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
        if response.status_code == 400 and schema is not None:
            payload["format"] = "json"  # Ollama older than 0.5: no JSON-schema format
            response = await client.post(url, json=payload)
        response.raise_for_status()
    return _parse(response.json().get("message", {}).get("content", ""))


async def chat_json(system: str, user: str, schema: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Ask the first available LLM for a JSON object; returns ``(data, provider label)``."""
    errors = []
    if settings.online_ai_enabled and settings.online_ai_api_key:
        try:
            return await _online(system, user), online_label()
        except Exception as exc:  # noqa: BLE001 - fall through to the next tier
            logger.warning("Online AI failed: %s", exc)
            errors.append(f"online AI: {exc}")
    if await _ollama_reachable():
        try:
            return await _ollama(system, user, schema), f"Ollama ({settings.ollama_model})"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ollama failed: %s", exc)
            errors.append(f"Ollama: {exc}")
    else:
        errors.append(f"Ollama not reachable at {settings.ollama_base_url}")
    raise LLMUnavailableError("; ".join(errors) or "no LLM configured")
