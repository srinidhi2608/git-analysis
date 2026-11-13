"""Extra context for a deep Story Analyzer run (only when the user asks for it).

* **Parents**: the story's parent work items, up to two levels (usually the
  Feature and its Epic): title, state, description, acceptance criteria and
  attachments. Reloaded from Azure DevOps when it is configured.
* **Attachments**: files attached to the story and to those parents, downloaded
  from Azure DevOps; Word (.docx), PDF and text files are read.
* **Uploads**: documents the user uploads for this analysis (not stored).

Every document is listed with its source and whether its text was used or why
it was skipped, so the page shows exactly what the LLM saw.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.devops_config import DevOpsConfig
from app.devops_ingest import upsert_work_item
from app.document_text import MAX_DOCUMENT_BYTES, DocumentText, extract_text, is_supported
from app.models import AzureWorkItem, AzureWorkItemRelation

logger = logging.getLogger(__name__)

MAX_PARENT_LEVELS = 2
MAX_ATTACHMENTS = 10
MAX_UPLOADS = 5
# Characters of context text sent to the LLM in total (parents + documents).
CONTEXT_BUDGET_CHARS = 30_000
PARENT_TEXT_CHARS = 4_000
MIN_DOCUMENT_CHARS = 2_000


@dataclass
class ContextOptions:
    include_parents: bool = True
    include_attachments: bool = True
    # Check the code of the linked GitHub PRs against the acceptance criteria.
    include_code: bool = False
    # Check the linked TestRail test cases, results and evidence against the acceptance criteria.
    include_tests: bool = False
    # (file name, content) of documents uploaded by the user.
    uploads: list[tuple[str, bytes]] = field(default_factory=list)


def _attachments(item: AzureWorkItem) -> list[AzureWorkItemRelation]:
    return [link for link in item.relations if link.rel == "AttachedFile"]


def _parent_id(item: AzureWorkItem) -> int | None:
    if item.parent_id:
        return item.parent_id
    for link in item.relations:
        if link.rel == "System.LinkTypes.Hierarchy-Reverse" and link.target_id:
            return link.target_id
    return None


async def _load_parent(db: Session, parent_id: int, devops: DevOpsConfig | None, warnings: list[str]) -> AzureWorkItem | None:
    """The parent work item, reloaded from Azure DevOps when configured."""
    if devops is not None and devops.configured:
        from app.azure_devops import AzureDevOpsClient

        client = AzureDevOpsClient(devops)
        stored = db.get(AzureWorkItem, parent_id)
        try:
            detail = await asyncio.to_thread(client.fetch_work_item, stored.project if stored else None, parent_id)
        except Exception as exc:  # noqa: BLE001 - fall back to the stored copy
            warnings.append(f"Could not load parent #{parent_id} from Azure DevOps ({exc}).")
            return stored
        parent = upsert_work_item(db, detail["item"])
        db.flush()
        parent.relations.clear()
        db.flush()
        for link in detail["relations"]:
            parent.relations.append(
                AzureWorkItemRelation(rel=link["rel"][:255], name=(link.get("name") or "")[:255] or None, url=link["url"][:1024], target_id=link.get("target_id"))
            )
        db.commit()
        return parent
    parent = db.get(AzureWorkItem, parent_id)
    if parent is None:
        warnings.append(f"Parent #{parent_id} is not stored and Azure DevOps is not configured, so it was not included.")
    return parent


async def _download(devops: DevOpsConfig | None, url: str, name: str) -> DocumentText:
    if devops is None or not devops.configured:
        return DocumentText(name, skipped="Azure DevOps is not configured, so attachments cannot be downloaded")
    from app.azure_devops import AzureDevOpsClient

    client = AzureDevOpsClient(devops)
    try:
        data = await asyncio.to_thread(client.download_attachment, url, MAX_DOCUMENT_BYTES)
    except Exception as exc:  # noqa: BLE001
        return DocumentText(name, skipped=f"download failed ({exc})")
    return await asyncio.to_thread(extract_text, name, data)


async def gather_context(
    db: Session,
    item: AzureWorkItem,
    devops: DevOpsConfig | None,
    options: ContextOptions,
    html_to_text,
) -> dict[str, Any]:
    """Parents and documents for a deep analysis (see module docstring)."""
    warnings: list[str] = []
    parents: list[dict[str, Any]] = []
    holders: list[tuple[str, AzureWorkItem]] = [(f"{item.work_item_type or 'Story'} #{item.id}", item)]

    if options.include_parents:
        current, seen = item, {item.id}
        for _ in range(MAX_PARENT_LEVELS):
            parent_id = _parent_id(current)
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            parent = await _load_parent(db, parent_id, devops, warnings)
            if parent is None:
                break
            description, _, _ = html_to_text(parent.description)
            criteria, _, _ = html_to_text(parent.acceptance_criteria)
            parents.append(
                {
                    "id": parent.id, "type": parent.work_item_type, "title": parent.title, "state": parent.state, "url": parent.url,
                    "description": description, "acceptance_criteria": criteria,
                    "attachments": [link.name or link.url.rsplit("/", 1)[-1] for link in _attachments(parent)],
                }
            )
            holders.append((f"{parent.work_item_type or 'Parent'} #{parent.id}", parent))
            current = parent
        if not parents:
            warnings.append("The story has no parent work item (Feature) to include.")

    documents: list[dict[str, Any]] = []
    texts: list[tuple[dict[str, Any], str]] = []

    def record(source: str, result: DocumentText, data_hash: str | None) -> None:
        meta = {
            "name": result.name, "source": source, "used": result.used, "skipped": result.skipped,
            "characters": len(result.text), "sha256": data_hash,
        }
        documents.append(meta)
        if result.used:
            texts.append((meta, result.text))

    if options.include_attachments:
        downloads = 0
        for source, holder in holders:
            for link in _attachments(holder):
                name = link.name or link.url.rsplit("/", 1)[-1]
                if not is_supported(name):
                    record(source, extract_text(name, b""), None)  # reports why the type is not read
                    continue
                if downloads >= MAX_ATTACHMENTS:
                    record(source, DocumentText(name, skipped=f"more than {MAX_ATTACHMENTS} attachments; not read"), None)
                    continue
                downloads += 1
                result = await _download(devops, link.url, name)
                record(source, result, hashlib.sha256(result.text.encode()).hexdigest() if result.used else None)

    for name, data in options.uploads[:MAX_UPLOADS]:
        result = await asyncio.to_thread(extract_text, name, data)
        record("uploaded", result, hashlib.sha256(data).hexdigest())

    return {"parents": parents, "documents": documents, "texts": texts, "warnings": warnings}


def context_prompt_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Parents and document texts for the LLM, within ``CONTEXT_BUDGET_CHARS``."""
    clip = lambda text, limit: text if len(text) <= limit else text[:limit] + " …[truncated]"  # noqa: E731
    parents = [
        {**{k: v for k, v in parent.items() if k not in ("description", "acceptance_criteria", "url")},
         "description": clip(parent["description"], PARENT_TEXT_CHARS), "acceptance_criteria": clip(parent["acceptance_criteria"], PARENT_TEXT_CHARS)}
        for parent in context["parents"]
    ]
    used = sum(len(p["description"]) + len(p["acceptance_criteria"]) for p in parents)
    remaining = max(CONTEXT_BUDGET_CHARS - used, MIN_DOCUMENT_CHARS)
    per_document = max(remaining // max(len(context["texts"]), 1), MIN_DOCUMENT_CHARS)
    return {
        "parents": parents,
        "documents": [{"name": meta["name"], "source": meta["source"], "text": clip(text, per_document)} for meta, text in context["texts"]],
        "documents_not_read": [{"name": d["name"], "source": d["source"], "reason": d["skipped"]} for d in context["documents"] if not d["used"]],
    }
