"""Story lifecycle settings from config.yaml (`lifecycle` section).

Maps Azure Boards states to ordered *stages*, so the app knows when a story
was in development, in testing, released or closed, and when it moved
backwards (rework). Example (also the default)::

    lifecycle:
      story_types: ["User Story", "Product Backlog Item"]
      stages:                       # workflow order; each state belongs to one stage
        - {name: new,      label: "New",               kind: wait,     states: ["New"]}
        - {name: dev,      label: "Development",       kind: work,     states: ["Active"]}
        - {name: qa,       label: "QA (lower env)",    kind: work,     states: ["QA Dev In Progress"]}
        - {name: qa_done,  label: "QA complete",       kind: wait,     states: ["QA Dev Complete"]}
        - {name: qaa,      label: "QAA testing",       kind: work,     states: ["QAA In Progress"]}
        - {name: qaa_done, label: "QAA complete",      kind: wait,     states: ["QAA Complete"]}
        - {name: int,      label: "INT",               kind: work,     states: ["INT"]}
        - {name: released, label: "Released",          kind: released, states: ["Released"]}
        - {name: closed,   label: "Closed",            kind: closed,   states: ["Closed", "Removed"]}
      release_tag_pattern: '(?i)^release\\s*v?(\\d+(?:\\.\\d+){0,2})$'
      testing:
        enabled: false

Stage kinds:

* ``work``     someone is actively working (development or testing) - counts as active time
* ``wait``     queued between steps (not started, or done and waiting for the next step)
* ``released`` deployed to production; the story is finished
* ``closed``   finished without a release (documentation, spike, abandoned work)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

STAGE_KINDS = ("wait", "work", "released", "closed")
TERMINAL_KINDS = ("released", "closed")
DEFAULT_STORY_TYPES = ["User Story", "Product Backlog Item"]
DEFAULT_RELEASE_TAG_PATTERN = r"(?i)^release\s*v?(\d+(?:\.\d+){0,2})$"


@dataclass(frozen=True)
class Stage:
    name: str
    label: str
    kind: str
    states: tuple[str, ...]
    order: int

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS


DEFAULT_STAGES = [
    {"name": "new", "label": "New", "kind": "wait", "states": ["New"]},
    {"name": "dev", "label": "Development", "kind": "work", "states": ["Active"]},
    {"name": "qa", "label": "QA (lower env)", "kind": "work", "states": ["QA Dev In Progress"]},
    {"name": "qa_done", "label": "QA complete", "kind": "wait", "states": ["QA Dev Complete"]},
    {"name": "qaa", "label": "QAA testing", "kind": "work", "states": ["QAA In Progress"]},
    {"name": "qaa_done", "label": "QAA complete", "kind": "wait", "states": ["QAA Complete"]},
    {"name": "int", "label": "INT", "kind": "work", "states": ["INT"]},
    {"name": "released", "label": "Released", "kind": "released", "states": ["Released"]},
    {"name": "closed", "label": "Closed", "kind": "closed", "states": ["Closed", "Removed"]},
]


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass
class LifecycleConfig:
    stages: list[Stage] = field(default_factory=list)
    story_types: list[str] = field(default_factory=lambda: list(DEFAULT_STORY_TYPES))
    release_tag_pattern: str = DEFAULT_RELEASE_TAG_PATTERN
    testing_enabled: bool = False

    def __post_init__(self) -> None:
        self._by_state = {state.lower(): stage for stage in self.stages for state in stage.states}
        try:
            self._release_tag = re.compile(self.release_tag_pattern)
        except re.error as exc:
            logger.warning("Invalid lifecycle.release_tag_pattern %r (%s); using the default.", self.release_tag_pattern, exc)
            self._release_tag = re.compile(DEFAULT_RELEASE_TAG_PATTERN)

    def stage_for(self, state: str | None) -> Stage | None:
        return self._by_state.get((state or "").strip().lower())

    def stage(self, name: str) -> Stage | None:
        return next((stage for stage in self.stages if stage.name == name), None)

    @property
    def dev_stage(self) -> Stage | None:
        """The first ``work`` stage: development."""
        return next((stage for stage in self.stages if stage.kind == "work"), None)

    @property
    def testing_stages(self) -> list[Stage]:
        """Non-terminal stages after development (QA, QAA, INT and their waiting states)."""
        dev = self.dev_stage
        if dev is None:
            return []
        return [stage for stage in self.stages if stage.order > dev.order and not stage.is_terminal]

    def is_story_type(self, work_item_type: str | None) -> bool:
        return (work_item_type or "").lower() in {item.lower() for item in self.story_types}

    def release_version(self, tags: list[str]) -> str | None:
        """Version from the first tag like ``Release 2026.10.1`` (``None`` if no tag matches)."""
        for tag in tags:
            match = self._release_tag.search(tag.strip())
            if match:
                return match.group(1) if match.groups() and match.group(1) else tag.strip()
        return None

    def describe(self) -> dict:
        return {
            "stages": [
                {"name": s.name, "label": s.label, "kind": s.kind, "states": list(s.states), "order": s.order}
                for s in self.stages
            ],
            "story_types": self.story_types,
            "release_tag_pattern": self.release_tag_pattern,
            "testing_enabled": self.testing_enabled,
        }


def _parse_stages(raw: object) -> list[Stage]:
    entries = raw if isinstance(raw, list) and raw else DEFAULT_STAGES
    stages: list[Stage] = []
    seen_states: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            logger.warning("Ignoring lifecycle stage %r: expected a mapping with a name.", entry)
            continue
        kind = str(entry.get("kind") or "work").lower()
        if kind not in STAGE_KINDS:
            logger.warning("lifecycle stage %r has unknown kind %r; using 'work'.", entry["name"], kind)
            kind = "work"
        states = []
        for state in _string_list(entry.get("states")):
            if state.lower() in seen_states:
                logger.warning("State %r is listed in stages %r and %r; keeping the first.", state, seen_states[state.lower()], entry["name"])
                continue
            seen_states[state.lower()] = str(entry["name"])
            states.append(state)
        name = str(entry["name"]).strip()
        stages.append(Stage(name, str(entry.get("label") or name), kind, tuple(states), len(stages)))
    if not stages:
        return _parse_stages(DEFAULT_STAGES)
    return stages


def parse_lifecycle_config(data: dict | None) -> LifecycleConfig:
    raw = (data or {}).get("lifecycle") or {}
    if not isinstance(raw, dict):
        logger.warning("Ignoring 'lifecycle' in config.yaml: expected a mapping.")
        raw = {}
    testing = raw.get("testing") or {}
    return LifecycleConfig(
        stages=_parse_stages(raw.get("stages")),
        story_types=_string_list(raw.get("story_types")) or list(DEFAULT_STORY_TYPES),
        release_tag_pattern=str(raw.get("release_tag_pattern") or DEFAULT_RELEASE_TAG_PATTERN),
        testing_enabled=bool(testing.get("enabled")) if isinstance(testing, dict) else False,
    )


def load_lifecycle_config(config_path: str = "config.yaml") -> LifecycleConfig:
    path = Path(config_path)
    data = None
    if path.exists():
        with path.open("r") as handle:
            data = yaml.safe_load(handle)
    return parse_lifecycle_config(data)
