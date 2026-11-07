"""Links between Azure Boards work items, GitHub pull requests, commits and pipeline runs.

One index answers, for every page:

* which work items (and which *stories*) a PR belongs to, and how it was linked
  (see ``app.story_lifecycle.link_pull_requests`` for the PR link sources);
* which work items a pipeline run built, from three sources:

  - ``Azure (build)``   work items Azure DevOps associates with the run (Builds API ``workitems``)
  - ``AB# in commit``   ``AB#<id>`` in the messages of the commits the run built (Builds API ``changes``)
  - ``via PR``          the run's commit (or one of its commits) belongs to a PR linked to the work item

A task or bug that is a child of a story resolves to that story.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.lifecycle_config import LifecycleConfig
from app.models import AzurePipelineRun, AzureWorkItem, Commit, PullRequest, Repository
from app.story_lifecycle import AB_REFERENCE, link_pull_requests


@dataclass
class WorkItemLink:
    id: int
    type: str | None
    title: str | None
    state: str | None
    url: str | None
    story_id: int | None
    sources: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "story_id": self.story_id,
            "sources": sorted(self.sources),
        }


def _ids(text: str | None) -> list[int]:
    return [int(value) for value in (text or "").split(",") if value.strip().isdigit()]


class LinkIndex:
    def __init__(self, db: Session, lifecycle: LifecycleConfig) -> None:
        items = db.query(AzureWorkItem).options(selectinload(AzureWorkItem.relations)).all()
        self.items = {item.id: item for item in items}
        self.lifecycle = lifecycle
        self.by_pr: dict[int, dict[int, WorkItemLink]] = defaultdict(dict)
        for work_item_id, prs in link_pull_requests(db, items).items():
            for pr_id, link in prs.items():
                self._add(self.by_pr[pr_id], work_item_id, link.sources)
        # AB#<id> mentions of work items that are not stored (not synced, or another project).
        self.unresolved_by_pr: dict[int, list[int]] = {}
        self.pr_labels: dict[int, str] = {}
        self.pr_by_sha: dict[str, int] = {}
        for pr_id, number, title, refs, merge_sha, repo in (
            db.query(PullRequest.id, PullRequest.pr_number, PullRequest.title, PullRequest.work_item_refs, PullRequest.merge_commit_sha, Repository.name)
            .join(Repository, PullRequest.repo_id == Repository.id)
        ):
            self.pr_labels[pr_id] = f"{repo}#{number}"
            if merge_sha:
                self.pr_by_sha[merge_sha.lower()] = pr_id
            mentioned = set(_ids(refs))
            mentioned |= {int(match) for match in AB_REFERENCE.findall(title or "")}
            missing = sorted(item for item in mentioned if item not in self.items)
            if missing:
                self.unresolved_by_pr[pr_id] = missing
        for pr_id, commit_hash in db.query(Commit.pr_id, Commit.commit_hash):
            if commit_hash:
                self.pr_by_sha.setdefault(commit_hash.lower(), pr_id)

    # -- helpers ---------------------------------------------------------------

    def story_of(self, work_item_id: int) -> int | None:
        item = self.items.get(work_item_id)
        if item is None:
            return None
        if self.lifecycle.is_story_type(item.work_item_type):
            return item.id
        parent = self.items.get(item.parent_id) if item.parent_id else None
        if parent is not None and self.lifecycle.is_story_type(parent.work_item_type):
            return parent.id
        return None

    def _add(self, target: dict[int, WorkItemLink], work_item_id: int, sources: set[str]) -> None:
        item = self.items.get(work_item_id)
        if item is None:
            return
        link = target.setdefault(
            work_item_id,
            WorkItemLink(item.id, item.work_item_type, item.title, item.state, item.url, self.story_of(item.id)),
        )
        link.sources |= set(sources)

    # -- pull requests ---------------------------------------------------------

    def for_pr(self, pr_id: int) -> list[WorkItemLink]:
        return sorted(self.by_pr.get(pr_id, {}).values(), key=lambda link: link.id)

    def stories_for_pr(self, pr_id: int) -> list[int]:
        return sorted({link.story_id for link in self.for_pr(pr_id) if link.story_id})

    def prs_for_work_item(self, work_item_id: int) -> list[int]:
        return sorted(pr_id for pr_id, links in self.by_pr.items() if work_item_id in links)

    # -- pipeline runs ---------------------------------------------------------

    def for_run(self, run: AzurePipelineRun) -> list[WorkItemLink]:
        links: dict[int, WorkItemLink] = {}
        for work_item_id in _ids(run.build_work_item_ids):
            self._add(links, work_item_id, {"Azure (build)"})
        for work_item_id in _ids(run.change_work_item_refs):
            self._add(links, work_item_id, {"AB# in commit"})
        shas = {sha.strip().lower() for sha in (run.change_shas or "").split(",") if sha.strip()}
        if run.source_version:
            shas.add(run.source_version.lower())
        for pr_id in {self.pr_by_sha[sha] for sha in shas if sha in self.pr_by_sha}:
            for link in self.by_pr.get(pr_id, {}).values():
                self._add(links, link.id, {f"via PR {self.pr_labels.get(pr_id, pr_id)}"})
        return sorted(links.values(), key=lambda link: link.id)
