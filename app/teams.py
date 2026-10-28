"""Team membership from config.yaml.

Example::

    teams:
      GrowthTeam:
        - name: "CF-Vaishnavi"
        - name: "CF-SinchanaBhat"
      IppPayFac:
        - name: "CF-NewMember"

When a ``teams`` section is present only listed members are ingested and shown;
data authored by anyone else is skipped and reported in the logs. Without a
``teams`` section every GitHub user is ingested (single, unassigned team).
GitHub logins are case-insensitive, so matching is too.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Data kinds tracked for skipped (non-member) authors, in log order.
SKIP_KINDS = ("pull_request", "review", "comment", "commit")


@dataclass
class TeamConfig:
    """Mapping of team name -> member logins, as configured."""

    teams: dict[str, list[str]] = field(default_factory=dict)
    _member_team: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.teams)

    @property
    def team_names(self) -> list[str]:
        return list(self.teams)

    def team_for(self, login: str | None) -> str | None:
        if not login:
            return None
        return self._member_team.get(login.lower())

    def is_member(self, login: str | None) -> bool:
        """True when ``login`` may be ingested (always true if no teams are configured)."""
        if not self.enabled:
            return True
        return self.team_for(login) is not None


def _member_name(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def parse_team_config(data: dict | None) -> TeamConfig:
    raw_teams = (data or {}).get("teams") or {}
    if not isinstance(raw_teams, dict):
        logger.warning("Ignoring 'teams' in config.yaml: expected a mapping of team name to member list.")
        return TeamConfig()

    config = TeamConfig()
    for team_name, members in raw_teams.items():
        team = str(team_name).strip()
        if not team:
            continue
        config.teams[team] = []
        for entry in members or []:
            login = _member_name(entry)
            if login is None:
                logger.warning("Ignoring invalid member entry %r in team %s.", entry, team)
                continue
            existing_team = config._member_team.get(login.lower())
            if existing_team is not None:
                logger.warning(
                    "%s is listed in both %s and %s; using %s.", login, existing_team, team, existing_team
                )
                continue
            config._member_team[login.lower()] = team
            config.teams[team].append(login)
    return config


def load_team_config(config_path: str = "config.yaml") -> TeamConfig:
    path = Path(config_path)
    if not path.exists():
        return TeamConfig()
    with path.open("r") as f:
        return parse_team_config(yaml.safe_load(f))


class SkippedAuthors:
    """Collects GitHub users whose data was skipped because they are not team members."""

    def __init__(self) -> None:
        self._counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._repositories: defaultdict[str, set[str]] = defaultdict(set)

    def record(self, login: str | None, kind: str, repository: str) -> None:
        name = login or "<deleted user>"
        self._counts[name][kind] += 1
        self._repositories[name].add(repository)

    def __bool__(self) -> bool:
        return bool(self._counts)

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {login: dict(counts) for login, counts in self._counts.items()}

    def log_summary(self) -> None:
        if not self._counts:
            return
        logger.warning(
            "Skipped data from %d GitHub user(s) not listed under 'teams' in config.yaml:",
            len(self._counts),
        )
        for login in sorted(self._counts, key=str.lower):
            counts = self._counts[login]
            details = ", ".join(f"{counts[kind]} {kind}(s)" for kind in SKIP_KINDS if counts[kind])
            logger.warning(
                "  not in any team: %s (%s) in %s",
                login,
                details,
                ", ".join(sorted(self._repositories[login])),
            )
