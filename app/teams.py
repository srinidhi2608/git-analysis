"""Team membership and identities from config.yaml.

Each member is one person, who may use different names in GitHub and Azure DevOps::

    teams:
      GrowthTeam:
        - name: "Vaishnavi K"                       # display name shown on every page
          github: "CF-Vaishnavi"                    # GitHub login
          azure_devops: "vaishnavi.k@company.com"   # Azure DevOps identity: email, unique name
                                                    # or display name (string or list)
          role: developer                           # optional: developer | tester
        - name: "Tom QA"                            # a tester without a GitHub account
          azure_devops: ["tom.q@company.com", "Tom Quinn"]
          role: tester
      IppPayFac:
        - name: "CF-NewMember"                      # short form: name is the GitHub login

Older configs keep working: an entry with neither ``github`` nor
``azure_devops`` uses ``name`` as the GitHub login, and ``azure`` is accepted as
an alias of ``azure_devops``.

When a ``teams`` section is present only listed members are ingested and shown;
data authored by anyone else is skipped and reported in the logs. Without a
``teams`` section every GitHub user is ingested (single, unassigned team).
Logins and identities are matched case-insensitively.
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
SKIP_KINDS = ("pull_request", "review", "comment", "commit", "work_item")


@dataclass
class Member:
    """One person, with their identities in GitHub and Azure DevOps."""

    key: str  # stable id used in URLs/filters: the GitHub login, else the display name
    display_name: str
    team: str
    github: str | None = None
    azure_devops: list[str] = field(default_factory=list)
    role: str | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "team": self.team,
            "github": self.github,
            "azure_devops": list(self.azure_devops),
            "role": self.role,
        }


@dataclass
class TeamConfig:
    """Teams and their members, as configured."""

    # team name -> member keys (GitHub logins, or display names for members without GitHub)
    teams: dict[str, list[str]] = field(default_factory=dict)
    _by_key: dict[str, Member] = field(default_factory=dict, repr=False)
    _by_github: dict[str, Member] = field(default_factory=dict, repr=False)
    _by_azure: dict[str, Member] = field(default_factory=dict, repr=False)
    _by_display_name: dict[str, Member] = field(default_factory=dict, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.teams)

    @property
    def team_names(self) -> list[str]:
        return list(self.teams)

    def member(self, key_or_login: str | None) -> Member | None:
        """Member by key or GitHub login."""
        if not key_or_login:
            return None
        lowered = key_or_login.lower()
        return self._by_github.get(lowered) or self._by_key.get(lowered)

    def team_for(self, login: str | None) -> str | None:
        member = self.member(login)
        return member.team if member else None

    def is_member(self, login: str | None) -> bool:
        """True when GitHub ``login`` may be ingested (always true if no teams are configured)."""
        if not self.enabled:
            return True
        return bool(login) and login.lower() in self._by_github

    def role_for(self, login: str | None) -> str | None:
        member = self.member(login)
        return member.role if member else None

    def display_name(self, key_or_login: str | None) -> str | None:
        """Configured display name for a member key or GitHub login (the input if not a member)."""
        member = self.member(key_or_login)
        return member.display_name if member else key_or_login

    def members(self) -> list[dict]:
        """All configured members with their identities, team and role."""
        return [self._by_key[key.lower()].as_dict() for keys in self.teams.values() for key in keys]

    def member_for_azure(self, *identities: str | None) -> Member | None:
        """The member behind an Azure DevOps identity (email, unique name or display name).

        Order: configured ``azure_devops`` identities, then the member's display
        name, then the GitHub login (or an email whose local part is the login).
        """
        for identity in identities:
            if not identity:
                continue
            lowered = identity.strip().lower()
            for lookup in (self._by_azure, self._by_display_name, self._by_github):
                if lowered in lookup:
                    return lookup[lowered]
            local_part = lowered.split("@", 1)[0]
            if "@" in lowered and local_part in self._by_github:
                return self._by_github[local_part]
        return None

    def login_for_azure(self, *identities: str | None) -> str | None:
        """Key of the member behind an Azure DevOps identity (their GitHub login if they have one)."""
        member = self.member_for_azure(*identities)
        return member.key if member else None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _identities(value: object) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def _parse_member(entry: object, team: str) -> Member | None:
    if isinstance(entry, str):
        login = _text(entry)
        return Member(key=login, display_name=login, team=team, github=login) if login else None
    if not isinstance(entry, dict):
        return None
    name = _text(entry.get("name")) or _text(entry.get("display_name"))
    # New format: `github` / `azure_devops` keys. Legacy short form (neither key):
    # `name` is the GitHub login.
    is_new_format = "github" in entry or "azure_devops" in entry
    github = _text(entry.get("github")) if is_new_format else name
    display_name = _text(entry.get("display_name")) or name or github
    if not display_name:
        return None
    azure = _identities(entry.get("azure_devops")) + _identities(entry.get("azure"))
    role = _text(entry.get("role"))
    return Member(
        key=github or display_name,
        display_name=display_name,
        team=team,
        github=github,
        azure_devops=azure,
        role=role.lower() if role else None,
    )


def parse_team_config(data: dict | None) -> TeamConfig:
    raw_teams = (data or {}).get("teams") or {}
    if not isinstance(raw_teams, dict):
        logger.warning("Ignoring 'teams' in config.yaml: expected a mapping of team name to member list.")
        return TeamConfig()

    config = TeamConfig()
    for team_name, entries in raw_teams.items():
        team = str(team_name).strip()
        if not team:
            continue
        config.teams[team] = []
        for entry in entries or []:
            member = _parse_member(entry, team)
            if member is None:
                logger.warning("Ignoring invalid member entry %r in team %s.", entry, team)
                continue
            existing = config._by_key.get(member.key.lower())
            if existing is not None:
                logger.warning(
                    "%s is listed in both %s and %s; using %s.", member.key, existing.team, team, existing.team
                )
                continue
            config._by_key[member.key.lower()] = member
            config.teams[team].append(member.key)
            if member.github:
                config._by_github[member.github.lower()] = member
            config._by_display_name.setdefault(member.display_name.lower(), member)
            for identity in member.azure_devops:
                other = config._by_azure.get(identity.lower())
                if other is not None and other is not member:
                    logger.warning(
                        "Azure DevOps identity %s is configured for both %s and %s; using %s.",
                        identity,
                        other.display_name,
                        member.display_name,
                        other.display_name,
                    )
                    continue
                config._by_azure[identity.lower()] = member
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
            "Skipped data from %d user(s) not listed under 'teams' in config.yaml:",
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
