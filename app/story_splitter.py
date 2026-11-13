"""Story creation: split a requirement into suggested user stories with the LLM.

Input: requirement text, optional uploaded documents (Word .docx, PDF, text)
and an optional parent Feature (its description and acceptance criteria are
used as context). The LLM follows the team's story rules
(``prompts/story-analysis.md``) and splitting rules
(``prompts/story-splitting.md``) and returns stories with a user-story
description, Given/When/Then acceptance criteria, an estimate, tags and
dependencies, plus open questions and assumptions.

Without an LLM, a rule-based draft makes one story per heading or top-level
bullet of the requirement. Nothing is written to Azure DevOps: the page offers
the stories as CSV (for Azure Boards import), Markdown or HTML.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.devops_config import DevOpsConfig
from app.document_text import DocumentText, extract_text
from app.llm_client import LLMUnavailableError, chat_json
from app.story_analyzer import html_to_text, load_instructions
from app.story_context import _load_parent

logger = logging.getLogger(__name__)

MAX_REQUIREMENT_CHARS = 30_000
MAX_DOCUMENT_CHARS = 12_000
MAX_STORIES = 15

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "story_points": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["title", "description", "acceptance_criteria"],
            },
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "stories", "open_questions", "assumptions"],
}

SPLIT_PROMPT = """You are an experienced product owner. Split the requirement below into user stories
that a team can deliver and test independently: vertical slices of user value (not layers such as
"backend" and "frontend"), each small enough for one sprint, following INVEST.
For every story give: a short title; a description starting "As a <role>, I want <goal> so that <benefit>"
plus the scope it covers; acceptance criteria as Given/When/Then, including failure and edge cases;
a story-point estimate (1, 2, 3, 5, 8 or 13); tags; dependencies on other stories by title; and notes.
Use only what the requirement, documents and parent feature say: list missing information as open
questions and anything you had to assume as assumptions. Do not invent integrations or numbers.
Return at most {max_stories} stories, in a sensible delivery order.
Reply with ONLY a JSON object:
{{"summary": str, "stories": [{{"title": str, "description": str, "acceptance_criteria": [str],
  "story_points": int, "tags": [str], "dependencies": [str], "notes": str}}],
  "open_questions": [str], "assumptions": [str]}}"""


def load_splitting_instructions() -> dict[str, Any]:
    return load_instructions(settings.story_splitting_instructions)


# ---------------------------------------------------------------------------
# Rule-based draft
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$")
_BULLET = re.compile(r"^(\s*)(?:[-*•]|\d+[.)])\s+(.+)$")


def heuristic_split(text: str, max_stories: int) -> list[dict[str, Any]]:
    """One draft story per heading (or top-level bullet) with its sub-points as criteria."""
    sections: list[tuple[str, list[str]]] = []
    lines = text.splitlines()
    if any(_HEADING.match(line) for line in lines):
        for line in lines:
            heading = _HEADING.match(line)
            if heading:
                sections.append((heading.group(1).strip(), []))
            elif sections and line.strip():
                sections[-1][1].append(line.strip(" -*•\t"))
    else:
        for line in lines:
            bullet = _BULLET.match(line)
            if bullet and len(bullet.group(1)) == 0:
                sections.append((bullet.group(2).strip(), []))
            elif bullet and sections:
                sections[-1][1].append(bullet.group(2).strip())
    if not sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        sections = [(p.split(".")[0][:80], [p]) for p in paragraphs]
    stories = []
    for title, points in sections[:max_stories]:
        title = title.rstrip(":").strip()
        stories.append(
            {
                "title": title[:120],
                "description": f"As a <role>, I want {title[0].lower() + title[1:] if title else 'this capability'} so that <benefit>.",
                "acceptance_criteria": [f"Given <context>, when <action>, then {point}" for point in points[:8]]
                or ["Given <context>, when <action>, then <observable result>"],
                "story_points": None,
                "tags": [],
                "dependencies": [],
                "notes": "Draft made from the requirement's structure (no LLM): complete the role, benefit and criteria.",
            }
        )
    return stories


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


def _clean_stories(raw: Any, max_stories: int) -> list[dict[str, Any]]:
    strings = lambda value: [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    stories = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        try:
            points = int(item["story_points"]) if item.get("story_points") not in (None, "") else None
        except (TypeError, ValueError):
            points = None
        stories.append(
            {
                "title": str(item["title"]).strip()[:255],
                "description": str(item.get("description") or "").strip(),
                "acceptance_criteria": strings(item.get("acceptance_criteria")),
                "story_points": points,
                "tags": strings(item.get("tags")),
                "dependencies": strings(item.get("dependencies")),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
    return stories[:max_stories]


async def split_requirement(
    db: Session,
    requirement: str,
    uploads: list[tuple[str, bytes]],
    parent_id: int | None = None,
    devops: DevOpsConfig | None = None,
    max_stories: int = 8,
) -> dict[str, Any]:
    max_stories = max(1, min(MAX_STORIES, max_stories))
    warnings: list[str] = []
    documents: list[DocumentText] = [await asyncio.to_thread(extract_text, name, data) for name, data in uploads]
    for document in documents:
        if not document.used:
            warnings.append(f"{document.name} was not read: {document.skipped}.")

    parent = None
    if parent_id:
        item = await _load_parent(db, parent_id, devops, warnings)
        if item is not None:
            description, _, _ = html_to_text(item.description)
            criteria, _, _ = html_to_text(item.acceptance_criteria)
            parent = {"id": item.id, "type": item.work_item_type, "title": item.title, "url": item.url,
                      "description": description, "acceptance_criteria": criteria}
        else:
            warnings.append(f"Parent #{parent_id} could not be loaded, so it was not used.")

    story_rules, split_rules = load_instructions(), load_splitting_instructions()
    system = SPLIT_PROMPT.format(max_stories=max_stories)
    if story_rules["used"]:
        system += "\n\nTEAM RULES FOR A GOOD STORY (follow these):\n" + story_rules["text"][:8000]
    if split_rules["used"]:
        system += "\n\nTEAM RULES FOR SPLITTING (follow these):\n" + split_rules["text"][:6000]
    payload = {
        "requirement": requirement[:MAX_REQUIREMENT_CHARS],
        "documents": [{"name": d.name, "text": d.text[:MAX_DOCUMENT_CHARS]} for d in documents if d.used],
        "parent_feature": parent,
    }

    provider = "Rule-based draft (no LLM)"
    summary, stories, questions, assumptions = None, [], [], []
    try:
        answer, provider = await chat_json(system, json.dumps(payload, ensure_ascii=False), SPLIT_SCHEMA)
        stories = _clean_stories(answer.get("stories"), max_stories)
        summary = answer.get("summary") if isinstance(answer.get("summary"), str) else None
        questions = [str(q) for q in answer.get("open_questions") or [] if str(q).strip()]
        assumptions = [str(a) for a in answer.get("assumptions") or [] if str(a).strip()]
        if not stories:
            warnings.append("The LLM returned no usable stories; showing a rule-based draft instead.")
            provider += " → rule-based draft"
    except LLMUnavailableError as exc:
        warnings.append("No LLM available, so these are rule-based drafts from the requirement's structure: " + str(exc))
    except Exception as exc:
        warnings.append(f"The LLM reply could not be used ({exc}); showing a rule-based draft.")
    if not stories:
        source = "\n".join([requirement, *(d.text for d in documents if d.used)])
        stories = heuristic_split(source, max_stories)

    return {
        "provider": provider,
        "warnings": warnings,
        "summary": summary or f"{len(stories)} suggested stor{'y' if len(stories) == 1 else 'ies'}.",
        "stories": stories,
        "open_questions": questions,
        "assumptions": assumptions,
        "parent": {k: parent[k] for k in ("id", "type", "title", "url")} if parent else None,
        "documents": [{"name": d.name, "used": d.used, "skipped": d.skipped, "characters": len(d.text)} for d in documents],
        "instructions": {"story_rules": story_rules["used"], "splitting_rules": split_rules["used"], "splitting_path": split_rules["path"]},
    }
