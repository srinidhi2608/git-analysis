"""Story Analyzer: quality review of one Azure Boards user story.

The analysis has three parts (see docs/story-analyzer.md):

* **Facts** gathered from Azure Boards and GitHub: description, acceptance
  criteria, attachments and inline images (evidence), discussion comments,
  child items, and the story lifecycle (stage timeline, PRs, rework, flags).
* **Content quality**: description, acceptance criteria, evidence and
  comments, each scored 0-100 with findings. Done by the configured LLM,
  following the team's instructions in ``prompts/story-analysis.md``; a
  rule-based review fills anything the LLM does not answer and is used on its
  own when no LLM is available.
* **Delivery health**: computed (never guessed) from the lifecycle: QA
  bounces, reopens, skipped stages, unmerged work, release tags, flow.

Health = 60% content quality + 40% delivery health. Results are cached per
story until the story, its comments or the instructions change.

On request (a checkbox on the page) a **deep analysis** also reads the parent
Feature/Epic, the attachments of the story and its parents, and documents the
user uploads, and reports how well the story matches them
(``app.story_context``). That context review is shown separately and does not
change the health score.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.devops_config import DevOpsConfig
from app.devops_ingest import replace_comments, upsert_work_item
from app.lifecycle_config import LifecycleConfig
from app.llm_client import LLMUnavailableError, chat_json
from app.models import AzureWorkItem, AzureWorkItemRelation, StoryAnalysis
from app.pr_code import MAX_PULL_REQUESTS, fetch_pr_changes, summarize_changes
from app.story_checks import (
    extend_prompt,
    extend_schema,
    heuristic_implementation,
    heuristic_test_coverage,
    merge_implementation,
    merge_test_coverage,
)
from app.story_context import ContextOptions, context_prompt_payload, gather_context
from app.testrail import gather_test_cases, load_testrail_config
from app.story_lifecycle import LinkedPr, build_lifecycle, link_pull_requests
from app.teams import TeamConfig
from app.time_window import utc_now_naive

logger = logging.getLogger(__name__)

SECTIONS = ("description", "acceptance_criteria", "evidence", "comments")
SECTION_WEIGHTS = {"description": 0.25, "acceptance_criteria": 0.35, "evidence": 0.2, "comments": 0.2}
CONTENT_WEIGHT, DELIVERY_WEIGHT = 0.6, 0.4
MAX_TEXT_CHARS = 6000
MAX_COMMENTS = 30
VAGUE_TERMS = (
    "etc", "and so on", "user friendly", "user-friendly", "fast", "quickly", "easy", "simple", "intuitive",
    "as needed", "appropriate", "properly", "should work", "works fine", "tbd", "to be decided", "some", "various",
)
EVIDENCE_NAME = re.compile(r"(test|screenshot|screen|evidence|report|result|recording|\.png|\.jpe?g|\.gif|\.pdf|\.mp4|\.har)", re.I)


class StoryNotFoundError(LookupError):
    pass


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------


def load_instructions(path: str | None = None) -> dict[str, Any]:
    """The team's analysis instructions (Markdown). HTML comments are ignored."""
    file = Path(path or settings.story_analysis_instructions)
    if not file.is_file():
        return {"path": str(file), "exists": False, "used": False, "text": ""}
    raw = file.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()
    return {"path": str(file), "exists": True, "used": bool(text), "text": text}


# ---------------------------------------------------------------------------
# HTML (Azure rich text) to plain text
# ---------------------------------------------------------------------------


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.images = 0
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "img":
            self.images += 1
            self.parts.append("[image]")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str | None) -> tuple[str, int, list[str]]:
    """(plain text, number of inline images, links) of an Azure rich-text field."""
    if not html:
        return "", 0, []
    parser = _Text()
    parser.feed(html)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, parser.images, parser.links


# ---------------------------------------------------------------------------
# Refresh from Azure DevOps
# ---------------------------------------------------------------------------


async def refresh_from_azure(db: Session, work_item_id: int, devops: DevOpsConfig) -> AzureWorkItem:
    """Fetch the work item (fields, links, attachments) and its comments, and store them."""
    from app.azure_devops import AzureDevOpsClient, AzureDevOpsError

    client = AzureDevOpsClient(devops)
    stored = db.get(AzureWorkItem, work_item_id)
    project = stored.project if stored else None

    def fetch() -> tuple[dict, list[dict]]:
        try:
            detail = client.fetch_work_item(project, work_item_id)
        except AzureDevOpsError as exc:
            if "not found" in str(exc).lower():
                raise StoryNotFoundError(f"Work item {work_item_id} was not found in Azure DevOps.") from exc
            raise
        comments = client.fetch_comments(detail["item"]["project"], work_item_id)
        return detail, comments

    detail, comments = await asyncio.to_thread(fetch)
    item = upsert_work_item(db, detail["item"])
    db.flush()
    item.relations.clear()
    db.flush()
    for link in detail["relations"]:
        item.relations.append(
            AzureWorkItemRelation(rel=link["rel"][:255], name=(link.get("name") or "")[:255] or None, url=link["url"][:1024], target_id=link.get("target_id"))
        )
    replace_comments(db, item, comments)
    db.commit()
    return item


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def gather_facts(db: Session, item: AzureWorkItem, lifecycle: LifecycleConfig, team_config: TeamConfig, now: datetime) -> dict[str, Any]:
    description, description_images, description_links = html_to_text(item.description)
    criteria, criteria_images, _ = html_to_text(item.acceptance_criteria)
    attachments = [
        {"name": link.name or link.url.rsplit("/", 1)[-1], "url": link.url}
        for link in item.relations
        if link.rel == "AttachedFile"
    ]
    hyperlinks = [link.url for link in item.relations if link.rel == "Hyperlink"]
    comments = []
    for comment in sorted(item.comments, key=lambda c: c.created_at or datetime.min)[-MAX_COMMENTS:]:
        text, images, _ = html_to_text(comment.text)
        comments.append(
            {"author": comment.author or comment.author_email, "author_email": comment.author_email,
             "created_at": comment.created_at.isoformat() + "Z" if comment.created_at else None, "text": text, "images": images}
        )

    children = db.query(AzureWorkItem).options(selectinload(AzureWorkItem.relations)).filter(AzureWorkItem.parent_id == item.id).all()
    links = link_pull_requests(db, [item, *children])
    prs: dict[int, LinkedPr] = dict(links.get(item.id, {}))
    for child in children:
        for pr_id, link in links.get(child.id, {}).items():
            prs.setdefault(pr_id, LinkedPr(link.pr, link.repository, link.author, set())).sources.add(f"via {child.work_item_type} #{child.id}")
    related_ids = [rel.target_id for rel in item.relations if rel.target_id and (rel.name or "").lower() == "related"]
    related = db.query(AzureWorkItem).filter(AzureWorkItem.id.in_(related_ids)).all() if related_ids else []
    row = build_lifecycle(item, lifecycle, team_config, prs, children, related, now)

    return {
        "story": {
            "id": item.id, "title": item.title, "type": item.work_item_type, "state": item.state, "project": item.project,
            "url": item.url, "tags": row["tags"], "story_points": item.story_points, "assignee": row["assignee"],
            "iteration_path": item.iteration_path, "area_path": item.area_path, "release_version": row["release_version"],
        },
        "description": {"text": description, "images": description_images, "links": description_links},
        "acceptance_criteria": {"text": criteria, "images": criteria_images},
        "attachments": attachments,
        "hyperlinks": hyperlinks,
        "comments": comments,
        "children": [{"id": c.id, "type": c.work_item_type, "title": c.title, "state": c.state} for c in children],
        "lifecycle": row,
    }


# ---------------------------------------------------------------------------
# Rule-based review (fallback, and fills gaps in the LLM answer)
# ---------------------------------------------------------------------------


def _section(score: float, assessment: str, strengths: list[str], issues: list[str]) -> dict[str, Any]:
    return {"score": int(max(0, min(100, round(score)))), "assessment": assessment, "strengths": strengths, "issues": issues}


def _criteria_lines(text: str) -> list[str]:
    lines = [line.strip(" -*•\t") for line in text.splitlines()]
    return [line for line in lines if len(line.split()) >= 3]


def heuristic_review(facts: dict[str, Any], testing_stage_names: set[str]) -> dict[str, Any]:
    row = facts["lifecycle"]
    suggestions: list[dict[str, str]] = []

    # Description
    text = facts["description"]["text"]
    words = len(text.split())
    strengths, issues = [], []
    if not text:
        description = _section(0, "There is no description.", [], ["The description is empty."])
        suggestions.append({"area": "Description", "priority": "high", "change": "Write a description: who needs this, what they need, and why.",
                            "example": "As a <type of user>, I want <goal> so that <benefit>."})
    else:
        score = 40 if words >= 20 else 20
        user_story = re.search(r"\bas an?\b.{0,80}\bi (want|need|can|would like)\b", text, re.I | re.S)
        value = re.search(r"\bso that\b|\bin order to\b", text, re.I)
        if user_story:
            score += 25
            strengths.append("Written as a user story (role and goal).")
        else:
            issues.append("Not written as a user story: the user role and goal are not stated.")
        if value:
            score += 15
            strengths.append("States the benefit (“so that …”).")
        else:
            issues.append("The business value (“so that …”) is missing.")
        if words >= 60:
            score += 10
        elif words < 20:
            issues.append(f"Very short ({words} words): context, scope and constraints are unclear.")
        if "\n- " in "\n" + text or facts["description"]["links"]:
            score += 10
            strengths.append("Structured, with lists or links to context.")
        description = _section(score, f"{words} words; " + ("clear user-story framing." if user_story else "framing as a user story is missing."), strengths, issues)
        if not user_story or not value:
            suggestions.append({"area": "Description", "priority": "medium", "change": "Start with the user story sentence so the role, goal and value are explicit.",
                                "example": "As a <role>, I want <capability> so that <outcome>."})

    # Acceptance criteria
    criteria = facts["acceptance_criteria"]["text"]
    lines = _criteria_lines(criteria)
    strengths, issues = [], []
    if not criteria:
        acceptance = _section(0, "There are no acceptance criteria.", [], ["Acceptance criteria are missing, so the story cannot be tested objectively."])
        suggestions.append({"area": "Acceptance criteria", "priority": "high", "change": "Add testable acceptance criteria, one behaviour each.",
                            "example": "Given <context>, when <action>, then <observable result>."})
    else:
        lowered = criteria.lower()
        gwt = all(word in lowered for word in ("given", "when", "then"))
        vague = sorted({term for term in VAGUE_TERMS if re.search(rf"\b{re.escape(term)}\b", lowered)})
        measurable = bool(re.search(r"\d", criteria))
        negative = bool(re.search(r"\b(error|invalid|fail|fails|denied|not |without|empty|timeout)\b", lowered))
        score = 40 + (25 if gwt else 0) + (15 if len(lines) >= 3 else 0) + (10 if measurable else 0) + (10 if negative else 0) - min(30, 10 * len(vague))
        if gwt:
            strengths.append("Uses Given / When / Then.")
        else:
            issues.append("Not in Given / When / Then form, so expected behaviour is harder to test.")
        if len(lines) < 3:
            issues.append(f"Only {len(lines)} criterion{'s' if len(lines) != 1 else ''}; edge cases are likely missing.")
        else:
            strengths.append(f"{len(lines)} criteria.")
        if not negative:
            issues.append("No error or negative scenarios (invalid input, failures, permissions).")
        if vague:
            issues.append("Vague wording that cannot be tested: " + ", ".join(f"“{term}”" for term in vague) + ".")
        if measurable:
            strengths.append("Contains measurable values.")
        acceptance = _section(score, f"{len(lines)} criteria" + (", Given/When/Then" if gwt else "") + ".", strengths, issues)
        if not gwt or not negative:
            suggestions.append({"area": "Acceptance criteria", "priority": "medium",
                                "change": "Rewrite each criterion as Given/When/Then and add at least one failure scenario.",
                                "example": "Given an expired card, when the user pays, then a clear error is shown and no order is created."})
        if vague:
            suggestions.append({"area": "Acceptance criteria", "priority": "medium", "change": "Replace vague terms with measurable expectations.",
                                "example": "“fast” → “the page loads in under 2 seconds for 95% of requests”."})

    # Evidence
    attachments = facts["attachments"]
    images = facts["description"]["images"] + facts["acceptance_criteria"]["images"] + sum(c["images"] for c in facts["comments"])
    test_like = [a["name"] for a in attachments if EVIDENCE_NAME.search(a["name"] or "")]
    merged = row["prs_merged"]
    reached_testing = row["outcome"] == "released" or any(name in row["stage_first_entered"] for name in testing_stage_names)
    strengths, issues = [], []
    if attachments or images:
        strengths.append(f"{len(attachments)} attachment(s), {images} inline image(s).")
    if test_like:
        strengths.append("Test evidence attached: " + ", ".join(test_like[:5]) + ".")
    if merged:
        strengths.append(f"{merged} merged pull request(s) linked.")
    if not reached_testing:
        evidence = _section(70, "Not tested yet, so evidence is not expected.", strengths, [] if merged or attachments else ["Nothing attached yet."])
    else:
        score = 30 + (30 if attachments or images else 0) + (20 if test_like else 0) + (20 if merged else 0)
        if not (attachments or images):
            issues.append("Tested or released, but no evidence is attached (screenshots, test results, recordings).")
        elif not test_like:
            issues.append("Attachments do not look like test evidence (no test report or screenshot names).")
        if not merged:
            issues.append("No merged pull request is linked to the story.")
        evidence = _section(score, "Tested/released story.", strengths, issues)
        if issues:
            suggestions.append({"area": "Evidence", "priority": "medium", "change": "Attach test evidence for each acceptance criterion and link the PRs.",
                                "example": "Screenshots or a test-run report named after the criterion, plus AB#<id> in the PR."})

    # Comments
    comments = facts["comments"]
    strengths, issues, questions = [], [], []
    for index, comment in enumerate(comments):
        if "?" in comment["text"] and not any(other["author"] != comment["author"] for other in comments[index + 1:]):
            questions.append(f"{comment['author']}: {comment['text'][:160]}")
    if not comments:
        comments_section = _section(50, "No discussion on the story.", [], ["No comments: decisions and clarifications are not recorded."])
    else:
        authors = {c["author"] for c in comments}
        score = 75 + (10 if len(authors) >= 2 else 0) - min(45, 15 * len(questions))
        strengths.append(f"{len(comments)} comment(s) from {len(authors)} people.")
        if questions:
            issues.append(f"{len(questions)} question(s) without a reply.")
        comments_section = _section(score, f"{len(comments)} comments.", strengths, issues)
        if questions:
            suggestions.append({"area": "Comments", "priority": "high", "change": "Answer the open questions and update the description or criteria with the decision.",
                                "example": questions[0]})
    insights = {"key_points": [], "unresolved_questions": questions}

    return {
        "sections": {"description": description, "acceptance_criteria": acceptance, "evidence": evidence, "comments": comments_section},
        "comments_insights": insights,
        "suggestions": suggestions,
        "suggested_acceptance_criteria": [],
        "suggested_tags": [],
        "summary": None,
    }


def delivery_health(row: dict[str, Any]) -> dict[str, Any]:
    """Score 0-100 from the lifecycle: 100 minus penalties for each problem found."""
    penalties: list[tuple[int, str]] = []
    if row["qa_bounces"]:
        penalties.append((min(30, 15 * row["qa_bounces"]), f"Sent back from testing {row['qa_bounces']}×"))
    if row["reopened"]:
        penalties.append((20, f"Reopened {row['reopened']}× after release/close"))
    if row["skipped_stages"]:
        penalties.append((15, "Skipped stages: " + ", ".join(row["skipped_stages"])))
    flags = set(row["flags"])
    if "closed_without_code" in flags:
        penalties.append((10, "Finished without a linked PR"))
    if "open_pr_at_finish" in flags:
        penalties.append((10, "A linked PR was still open when it finished"))
    if "abandoned_pr" in flags:
        penalties.append((5, "A linked PR was closed without merging"))
    if "released_without_release_tag" in flags:
        penalties.append((5, "Released without a “Release x.y.z” tag"))
    if "closed_unreleased_with_code" in flags:
        penalties.append((10, "Closed without release although code was merged"))
    if "unmapped_states" in flags:
        penalties.append((5, "Visited states missing from the stage mapping"))
    if row["flow_efficiency"] is not None and row["flow_efficiency"] < 0.4:
        penalties.append((10, f"Mostly waiting (flow efficiency {round(row['flow_efficiency'] * 100)}%)"))
    score = max(0, 100 - sum(points for points, _ in penalties))
    note = "No state history yet: sync Azure DevOps for delivery signals." if "no_history" in flags else None
    return {"score": score, "penalties": [{"points": p, "reason": r} for p, r in penalties], "note": note}


# ---------------------------------------------------------------------------
# LLM review
# ---------------------------------------------------------------------------

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "assessment": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "assessment", "strengths", "issues"],
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        **{name: SECTION_SCHEMA for name in SECTIONS},
        "comments_insights": {
            "type": "object",
            "properties": {
                "key_points": {"type": "array", "items": {"type": "string"}},
                "unresolved_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["key_points", "unresolved_questions"],
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "change": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["area", "priority", "change"],
            },
        },
        "suggested_acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "suggested_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", *SECTIONS, "comments_insights", "suggestions", "suggested_acceptance_criteria", "suggested_tags"],
}

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "assessment": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "sources_used": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "assessment", "gaps", "conflicts", "sources_used"],
}


def response_schema(deep: bool, code: bool = False, tests: bool = False) -> dict[str, Any]:
    schema = RESPONSE_SCHEMA
    if deep:
        schema = {
            **RESPONSE_SCHEMA,
            "properties": {**RESPONSE_SCHEMA["properties"], "context": CONTEXT_SCHEMA},
            "required": [*RESPONSE_SCHEMA["required"], "context"],
        }
    return extend_schema(schema, code, tests)


DEEP_PROMPT = """
DEEP ANALYSIS: the input also has "context": the story's parent work items (Feature, Epic) and the
text of documents attached to the story or its parents, or uploaded by the user. Use them to judge
whether the story is complete and consistent with what the feature and documents require:
requirements the story misses, details that contradict them, and acceptance criteria to add.
Cite the source (e.g. "Feature #12" or the document name) in issues and suggestions.
Also return:
 "context": {"score": int,          // 0-100: how well the story covers and agrees with its context
             "assessment": str,
             "gaps": [str],          // required by the feature/documents but missing from the story
             "conflicts": [str],     // where the story disagrees with the feature/documents
             "sources_used": [str]}  // parents and document names you relied on"""

SYSTEM_PROMPT = """You are an experienced agile coach and QA lead reviewing one Azure Boards user story.
Judge the quality of the story itself: its description, acceptance criteria, attached evidence and
discussion. Be specific: quote the story when pointing at a problem, and suggest concrete rewrites.
Score each area 0-100 (100 = excellent). Delivery facts (stages, rework, PRs) are given for context:
use them to judge evidence and to explain problems, but do not invent facts that are not in the input.
Reply with ONLY a JSON object with exactly these keys:
{"summary": str,
 "description": {"score": int, "assessment": str, "strengths": [str], "issues": [str]},
 "acceptance_criteria": {...same shape...}, "evidence": {...}, "comments": {...},
 "comments_insights": {"key_points": [str], "unresolved_questions": [str]},
 "suggestions": [{"area": str, "priority": "high"|"medium"|"low", "change": str, "example": str}],
 "suggested_acceptance_criteria": [str],   // rewritten criteria, Given/When/Then
 "suggested_tags": [str]}"""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def build_llm_input(
    facts: dict[str, Any],
    instructions: dict[str, Any],
    context: dict[str, Any] | None = None,
    code: list[dict[str, Any]] | None = None,
    tests: dict[str, Any] | None = None,
) -> tuple[str, str]:
    row = facts["lifecycle"]
    system = SYSTEM_PROMPT + (DEEP_PROMPT if context is not None else "")
    if instructions["used"]:
        system += "\n\nTEAM INSTRUCTIONS (follow these; they take precedence over general advice):\n" + _clip(instructions["text"], 8000)
    payload = {
        "story": facts["story"],
        "description": _clip(facts["description"]["text"], MAX_TEXT_CHARS),
        "description_inline_images": facts["description"]["images"],
        "acceptance_criteria": _clip(facts["acceptance_criteria"]["text"], MAX_TEXT_CHARS),
        "attachments": [a["name"] for a in facts["attachments"]],
        "hyperlinks": facts["hyperlinks"][:20],
        "comments": [{"author": c["author"], "date": (c["created_at"] or "")[:10], "text": _clip(c["text"], 800), "images": c["images"]} for c in facts["comments"]],
        "child_items": facts["children"][:30],
        "delivery": {
            "outcome": row["outcome"],
            "current_stage": row["stage"],
            "stage_hours": row["stage_hours"],
            "lead_time_hours": row["lead_time_hours"],
            "qa_bounces": row["qa_bounces"],
            "reopened": row["reopened"],
            "skipped_stages": row["skipped_stages"],
            "flags": row["flags"],
            "pull_requests": [{"number": p["number"], "title": p["title"], "status": p["status"]} for p in row["pull_requests"]][:20],
            "developers": [p["name"] for p in row["developers"]],
            "testers": [p["name"] for p in row["testers"]],
        },
    }
    if context is not None:
        payload["context"] = context_prompt_payload(context)
    system = extend_prompt(system, payload, code, tests)
    return system, json.dumps(payload, ensure_ascii=False, default=str)


def _valid_section(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        score = int(round(float(value.get("score"))))
    except (TypeError, ValueError):
        return None
    assessment = value.get("assessment")
    if not isinstance(assessment, str) or not assessment.strip():
        return None
    strings = lambda key: [str(item) for item in value.get(key) or [] if str(item).strip()]  # noqa: E731
    return {"score": max(0, min(100, score)), "assessment": assessment.strip(), "strengths": strings("strengths"), "issues": strings("issues")}


_STOPWORDS = set(
    "about above after again against because before being below between could during every should their there these "
    "those through under until where which while would within without other using based users customer customers "
    "story feature system support allow allows able ensure provide given when then".split()
)


def heuristic_context_review(facts: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Without an LLM: which frequent terms of the feature/documents the story never mentions."""
    parents, texts = context["parents"], context["texts"]
    sources = [f"{p['type']} #{p['id']}" for p in parents] + [meta["name"] for meta, _ in texts]
    if not sources:
        return {"score": None, "assessment": "No parent feature or readable documents to compare with.", "gaps": [], "conflicts": [], "sources_used": []}
    corpus = " ".join([p["description"] + " " + p["acceptance_criteria"] for p in parents] + [text for _, text in texts]).lower()
    story = (facts["story"]["title"] + " " + facts["description"]["text"] + " " + facts["acceptance_criteria"]["text"]).lower()
    counts: dict[str, int] = {}
    for word in re.findall(r"[a-z][a-z\-]{5,}", corpus):
        if word not in _STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    key_terms = [word for word, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:15]]
    missing = [term for term in key_terms if term not in story]
    score = round(100 * (len(key_terms) - len(missing)) / len(key_terms)) if key_terms else None
    gaps = [f"“{term}” appears in the feature/documents but not in the story" for term in missing[:8]]
    for parent in parents:
        if not parent["description"]:
            gaps.append(f"{parent['type']} #{parent['id']} has no description to check the story against.")
    return {
        "score": score,
        "assessment": f"Rule-based comparison: the story mentions {len(key_terms) - len(missing)} of the {len(key_terms)} most frequent terms of its context. An LLM review compares meaning, not words.",
        "gaps": gaps,
        "conflicts": [],
        "sources_used": sources,
    }


def merge_context(llm: dict[str, Any] | None, fallback: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The LLM's context review if usable, else the rule-based one; returns (review, from_llm)."""
    value = (llm or {}).get("context")
    if isinstance(value, dict) and isinstance(value.get("assessment"), str) and value["assessment"].strip():
        try:
            score = max(0, min(100, int(round(float(value.get("score"))))))
        except (TypeError, ValueError):
            score = fallback["score"]
        as_list = lambda key: [str(item) for item in value.get(key) or [] if str(item).strip()] if isinstance(value.get(key), list) else []  # noqa: E731
        return {"score": score, "assessment": value["assessment"].strip(), "gaps": as_list("gaps"),
                "conflicts": as_list("conflicts"), "sources_used": as_list("sources_used")}, True
    return fallback, False


def merge_reviews(llm: dict[str, Any] | None, fallback: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """LLM answer with gaps filled from the rule-based review; returns (review, areas filled)."""
    if not llm:
        return fallback, list(SECTIONS)
    filled = []
    sections = {}
    for name in SECTIONS:
        section = _valid_section(llm.get(name))
        if section is None:
            filled.append(name)
            section = fallback["sections"][name]
        sections[name] = section
    insights = llm.get("comments_insights") if isinstance(llm.get("comments_insights"), dict) else {}
    suggestions = [
        {
            "area": str(item.get("area") or "Story"),
            "priority": item.get("priority") if item.get("priority") in ("high", "medium", "low") else "medium",
            "change": str(item["change"]),
            "example": str(item.get("example") or ""),
        }
        for item in llm.get("suggestions") or []
        if isinstance(item, dict) and item.get("change")
    ]
    as_list = lambda value: [str(item) for item in value or [] if str(item).strip()] if isinstance(value, list) else []  # noqa: E731
    return {
        "sections": sections,
        "comments_insights": {
            "key_points": as_list(insights.get("key_points")),
            "unresolved_questions": as_list(insights.get("unresolved_questions")) or fallback["comments_insights"]["unresolved_questions"],
        },
        "suggestions": suggestions or fallback["suggestions"],
        "suggested_acceptance_criteria": as_list(llm.get("suggested_acceptance_criteria")),
        "suggested_tags": as_list(llm.get("suggested_tags")),
        "summary": llm.get("summary") if isinstance(llm.get("summary"), str) else None,
    }, filled


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


def gather_code(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Code changes of the story's linked PRs (merged first, at most ``MAX_PULL_REQUESTS``)."""
    prs = sorted(facts["lifecycle"]["pull_requests"], key=lambda pr: (pr["status"] != "merged", pr["status"] != "open"))
    changes = []
    for pr in prs[:MAX_PULL_REQUESTS]:
        change = fetch_pr_changes(pr["repository"], pr["number"])
        changes.append({**change, "title": pr["title"], "status": pr["status"]})
    return changes


def _level(score: int) -> str:
    return "Healthy" if score >= 75 else "Needs attention" if score >= 50 else "At risk"


def _input_hash(
    facts: dict[str, Any],
    instructions: dict[str, Any],
    context: dict[str, Any] | None = None,
    code: list[dict[str, Any]] | None = None,
    tests: dict[str, Any] | None = None,
) -> str:
    row = facts["lifecycle"]
    material = {
        "code": None if code is None else [
            (c["repository"], c["number"], c["error"], [(f["filename"], hashlib.sha256(f["patch"].encode()).hexdigest()) for f in c["files"]])
            for c in code
        ],
        "tests": None if tests is None else [tests.get("configured"), tests.get("cases")],
        "context": None if context is None else {
            "parents": context["parents"],
            "documents": [(d["name"], d["source"], d["used"], d["skipped"], d["sha256"]) for d in context["documents"]],
        },
        "story": facts["story"], "description": facts["description"], "criteria": facts["acceptance_criteria"],
        "attachments": facts["attachments"], "comments": facts["comments"], "children": facts["children"],
        "flags": row["flags"], "stage": row["stage"], "prs": [(p["number"], p["status"]) for p in row["pull_requests"]],
        "instructions": instructions["text"], "llm": [settings.online_ai_enabled, settings.online_ai_model, settings.ollama_model],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


async def analyze_story(
    db: Session,
    work_item_id: int,
    lifecycle: LifecycleConfig,
    team_config: TeamConfig,
    devops: DevOpsConfig | None = None,
    refresh: bool = False,
    use_cache: bool = True,
    now: datetime | None = None,
    context_options: ContextOptions | None = None,
) -> dict[str, Any]:
    """Analyze one story; ``refresh`` reloads it (and its comments) from Azure DevOps first.

    ``context_options`` turns on the deep analysis: parent Feature/Epic,
    attachments of the story and its parents, and uploaded documents.
    """
    now = now or utc_now_naive()
    warnings: list[str] = []
    item = db.get(AzureWorkItem, work_item_id)
    if devops is not None and devops.configured and (refresh or item is None):
        try:
            item = await refresh_from_azure(db, work_item_id, devops)
        except StoryNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - analyze the stored copy instead
            logger.warning("Could not refresh work item %s from Azure DevOps: %s", work_item_id, exc)
            warnings.append(f"Could not load the latest version from Azure DevOps ({exc}); using the stored copy.")
            db.rollback()
            item = db.get(AzureWorkItem, work_item_id)
    if item is None:
        raise StoryNotFoundError(
            f"Work item {work_item_id} is not stored" + ("" if devops and devops.configured else " and Azure DevOps is not configured") + "."
        )

    facts = gather_facts(db, item, lifecycle, team_config, now)
    instructions = load_instructions()
    if not instructions["used"]:
        warnings.append(f"No team instructions in {instructions['path']}: using the general review only.")
    context = code = tests = None
    if context_options is not None:
        context = await gather_context(db, item, devops, context_options, html_to_text)
        warnings.extend(context["warnings"])
        if context_options.include_code:
            code = await asyncio.to_thread(gather_code, facts)
            if not code:
                warnings.append("The story has no linked pull requests, so its code could not be checked.")
            warnings.extend(f"{c['repository']}#{c['number']}: {c['error']}" for c in code if c["error"])
        if context_options.include_tests:
            texts = [item.description or "", item.acceptance_criteria or "", *(c["text"] for c in facts["comments"]),
                     *facts["hyperlinks"], *(a["url"] for a in facts["attachments"])]
            tests = await asyncio.to_thread(gather_test_cases, load_testrail_config(), item.id, texts)
            if not tests["configured"]:
                warnings.append("TestRail is not configured, so test cases could not be checked: missing " + ", ".join(tests["missing"]) + ".")
            elif not tests["cases"]:
                warnings.append(f"No TestRail test cases reference story {item.id}.")
            warnings.extend(f"TestRail: {error}" for error in tests["errors"])
    input_hash = _input_hash(facts, instructions, context, code, tests)
    if use_cache:
        cached = (
            db.query(StoryAnalysis)
            .filter(StoryAnalysis.work_item_id == item.id, StoryAnalysis.input_hash == input_hash)
            .order_by(StoryAnalysis.created_at.desc())
            .first()
        )
        if cached is not None:
            result = json.loads(cached.result)
            result.update(cached=True, warnings=warnings + result.get("warnings", []))
            return result

    testing = {stage.name for stage in lifecycle.testing_stages}
    fallback = heuristic_review(facts, testing)
    llm_answer, provider, llm_error = None, "Rule-based (no LLM)", None
    try:
        system, user = build_llm_input(facts, instructions, context, code, tests)
        llm_answer, provider = await chat_json(system, user, response_schema(context is not None, code is not None, tests is not None))
    except LLMUnavailableError as exc:
        llm_error = str(exc)
        warnings.append("No LLM available, so this is the rule-based review: " + llm_error)
    except Exception as exc:  # noqa: BLE001 - a bad LLM reply must not break the page
        llm_error = str(exc)
        warnings.append(f"The LLM reply could not be used ({exc}); showing the rule-based review.")
    review, filled = merge_reviews(llm_answer, fallback)
    context_review = None
    if context is not None:
        context_review, from_llm = merge_context(llm_answer, heuristic_context_review(facts, context))
        if llm_answer is not None and not from_llm:
            filled.append("context")
    criteria_text = facts["acceptance_criteria"]["text"]
    implementation_review = test_review = None
    if code is not None:
        implementation_review, from_llm = merge_implementation(llm_answer, heuristic_implementation(criteria_text, code))
        if llm_answer is not None and not from_llm:
            filled.append("implementation")
    if tests is not None and tests["configured"]:
        test_review, from_llm = merge_test_coverage(llm_answer, heuristic_test_coverage(criteria_text, tests))
        if llm_answer is not None and not from_llm:
            filled.append("test coverage")
    if llm_answer is not None and filled:
        provider += " + rule-based for " + ", ".join(name.replace("_", " ") for name in filled)

    content = round(sum(review["sections"][name]["score"] * weight for name, weight in SECTION_WEIGHTS.items()))
    delivery = delivery_health(facts["lifecycle"])
    overall = round(CONTENT_WEIGHT * content + DELIVERY_WEIGHT * delivery["score"])
    row = facts["lifecycle"]
    result = {
        "story": facts["story"],
        "analyzed_at": now.isoformat() + "Z",
        "provider": provider,
        "cached": False,
        "warnings": warnings,
        "instructions": {"path": instructions["path"], "used": instructions["used"], "characters": len(instructions["text"])},
        "health": {
            "score": overall,
            "level": _level(overall),
            "content_score": content,
            "delivery_score": delivery["score"],
            "summary": review["summary"] or _default_summary(review, delivery, overall),
        },
        "sections": review["sections"],
        "comments_insights": review["comments_insights"],
        "delivery": delivery,
        "suggestions": review["suggestions"],
        "suggested_acceptance_criteria": review["suggested_acceptance_criteria"],
        "suggested_tags": review["suggested_tags"],
        "lifecycle": {
            key: row[key]
            for key in (
                "outcome", "stage", "timeline", "stage_hours", "lead_time_hours", "cycle_time_hours", "age_hours",
                "flow_efficiency", "rework_count", "qa_bounces", "reopened", "skipped_stages", "flags", "pull_requests",
                "developers", "testers", "bugs_linked", "escaped_defects", "created_at", "released_at",
            )
        },
        "stages": lifecycle.describe()["stages"],
        "inputs": {
            "description": facts["description"]["text"],
            "acceptance_criteria": facts["acceptance_criteria"]["text"],
            "attachments": facts["attachments"],
            "inline_images": facts["description"]["images"] + facts["acceptance_criteria"]["images"],
            "comments": facts["comments"],
            "children": facts["children"],
        },
        # Deep analysis only: what was read and how well the story matches it.
        "context": None if context is None else {
            "parents": [
                {key: parent[key] for key in ("id", "type", "title", "state", "url", "attachments")}
                | {"description": _clip(parent["description"], 1500)}
                for parent in context["parents"]
            ],
            "documents": [{key: d[key] for key in ("name", "source", "used", "skipped", "characters")} for d in context["documents"]],
            "review": context_review,
        },
        # Deep analysis, when chosen: linked PR code and TestRail test cases vs the acceptance criteria.
        "implementation": None if code is None else {"pull_requests": summarize_changes(code), "review": implementation_review},
        "test_coverage": None if tests is None else {
            "configured": tests["configured"],
            "missing": tests["missing"],
            "errors": tests["errors"],
            "cases": [
                {key: case[key] for key in ("id", "title", "url", "refs", "linked_by", "attachments")}
                | {"steps": len(case["steps"]), "latest": case["latest"]}
                for case in tests["cases"]
            ],
            "review": test_review,
        },
    }
    db.add(StoryAnalysis(work_item_id=item.id, input_hash=input_hash, provider=provider, created_at=now, result=json.dumps(result, default=str)))
    db.commit()
    return result


def _default_summary(review: dict[str, Any], delivery: dict[str, Any], overall: int) -> str:
    weakest = min(SECTIONS, key=lambda name: review["sections"][name]["score"])
    parts = [f"Overall {overall}/100 ({_level(overall).lower()}).", f"Weakest area: {weakest.replace('_', ' ')} ({review['sections'][weakest]['score']}/100)."]
    if delivery["penalties"]:
        parts.append("Delivery: " + "; ".join(p["reason"] for p in delivery["penalties"][:3]) + ".")
    return " ".join(parts)


def list_stories(db: Session, lifecycle: LifecycleConfig, search: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Stories for the picker, most recently changed first."""
    query = db.query(AzureWorkItem).filter(AzureWorkItem.work_item_type.in_(lifecycle.story_types))
    if search:
        term = search.strip()
        if term.isdigit():
            query = query.filter(AzureWorkItem.id == int(term)) if len(term) >= 3 else query.filter(AzureWorkItem.title.ilike(f"%{term}%"))
        else:
            query = query.filter(AzureWorkItem.title.ilike(f"%{term}%"))
    rows = query.order_by(AzureWorkItem.changed_date.desc().nullslast(), AzureWorkItem.id.desc()).limit(limit).all()
    analyzed = {
        work_item_id: created
        for work_item_id, created in db.query(StoryAnalysis.work_item_id, StoryAnalysis.created_at)
        .filter(StoryAnalysis.work_item_id.in_([row.id for row in rows]))
        .order_by(StoryAnalysis.created_at)
        .all()
    } if rows else {}
    return [
        {"id": row.id, "title": row.title, "state": row.state, "type": row.work_item_type, "assignee": row.assigned_to,
         "changed_date": row.changed_date.isoformat() + "Z" if row.changed_date else None,
         "last_analyzed": analyzed[row.id].isoformat() + "Z" if row.id in analyzed else None}
        for row in rows
    ]
