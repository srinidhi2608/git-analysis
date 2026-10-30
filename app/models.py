from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Developer(Base):
    __tablename__ = "developers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    github_username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    team_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    pull_requests: Mapped[list["PullRequest"]] = relationship("PullRequest", back_populates="developer")
    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="developer")


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    pull_requests: Mapped[list["PullRequest"]] = relationship("PullRequest", back_populates="repository")


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repo_id", "pr_number", name="uq_pull_requests_repo_pr_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    developer_id: Mapped[int] = mapped_column(Integer, ForeignKey("developers.id"), nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    user_story_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    merged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cycle_time_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_comments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviewers_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approvals_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_changes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_decision: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_review_comment_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_review_comment_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # GitHub's updatedAt when this PR was last ingested; an unchanged value lets a
    # re-sync skip downloading the PR's details again.
    github_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Target (base) branch the PR merges into, e.g. "main" or "release/2026.10".
    base_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    # Merge commit SHA, used to find the deployment that shipped this PR (DORA lead time).
    merge_commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # OPEN / CLOSED / MERGED on GitHub; CLOSED without merged_at = abandoned.
    state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    head_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # GitHub's numeric PR id (Azure Boards "GitHub Pull Request" links use it).
    github_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    # Azure work item ids mentioned as AB#<id> in the PR title or description, comma-separated.
    work_item_refs: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="pull_requests")
    developer: Mapped["Developer"] = relationship("Developer", back_populates="pull_requests")
    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="pull_request")
    comments: Mapped[list["PullRequestComment"]] = relationship("PullRequestComment", back_populates="pull_request")


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("pr_id", "commit_hash", name="uq_commits_pr_commit_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pr_id: Mapped[int] = mapped_column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    developer_id: Mapped[int] = mapped_column(Integer, ForeignKey("developers.id"), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    pull_request: Mapped["PullRequest"] = relationship("PullRequest", back_populates="commits")
    developer: Mapped["Developer"] = relationship("Developer", back_populates="commits")


class PullRequestComment(Base):
    __tablename__ = "pull_request_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pr_id: Mapped[int] = mapped_column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    comment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    author_login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    review_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    pull_request: Mapped["PullRequest"] = relationship("PullRequest", back_populates="comments")


# ---------------------------------------------------------------------------
# Azure DevOps (Boards work items and Pipelines runs)
# ---------------------------------------------------------------------------


class AzureWorkItem(Base):
    """A work item (user story, bug, task, ...) from Azure Boards."""

    __tablename__ = "azure_work_items"

    # Azure DevOps work item ids are unique within an organization.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    work_item_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_to_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    activated_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    changed_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    story_points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    iteration_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    area_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # System.ChangedDate when the state history was last downloaded (skip if unchanged).
    history_changed_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    tags: Mapped[list["AzureWorkItemTag"]] = relationship(
        "AzureWorkItemTag", back_populates="work_item", cascade="all, delete-orphan"
    )
    history: Mapped[list["AzureWorkItemStateChange"]] = relationship(
        "AzureWorkItemStateChange",
        back_populates="work_item",
        cascade="all, delete-orphan",
        order_by="AzureWorkItemStateChange.rev",
    )
    relations: Mapped[list["AzureWorkItemRelation"]] = relationship(
        "AzureWorkItemRelation", back_populates="work_item", cascade="all, delete-orphan"
    )


class AzureWorkItemStateChange(Base):
    """One revision of a work item that changed its state and/or assignee.

    ``state`` and ``assigned_to`` are the values in effect after the revision;
    ``from_state`` is set only when the state changed.
    """

    __tablename__ = "azure_work_item_state_changes"
    __table_args__ = (UniqueConstraint("work_item_id", "rev", name="uq_azure_work_item_state_change"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("azure_work_items.id"), nullable=False, index=True)
    rev: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    changed_by_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    from_state: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    assigned_to_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    work_item: Mapped["AzureWorkItem"] = relationship("AzureWorkItem", back_populates="history")


class AzureWorkItemRelation(Base):
    """A current link of a work item: parent/child/related work items, GitHub PRs and commits."""

    __tablename__ = "azure_work_item_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("azure_work_items.id"), nullable=False, index=True)
    rel: Mapped[str] = mapped_column(String(255), nullable=False)
    # Link type name, e.g. "Child", "Related", "GitHub Pull Request", "GitHub Commit".
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Linked work item id, for work item links.
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    work_item: Mapped["AzureWorkItem"] = relationship("AzureWorkItem", back_populates="relations")


class AzureWorkItemTag(Base):
    __tablename__ = "azure_work_item_tags"
    __table_args__ = (UniqueConstraint("work_item_id", "tag", name="uq_azure_work_item_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("azure_work_items.id"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    work_item: Mapped["AzureWorkItem"] = relationship("AzureWorkItem", back_populates="tags")


class AzurePipelineRun(Base):
    """A completed Azure Pipelines build/run (deployments are identified by pipeline name patterns)."""

    __tablename__ = "azure_pipeline_runs"
    __table_args__ = (UniqueConstraint("project", "build_id", name="uq_azure_pipeline_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    build_id: Mapped[int] = mapped_column(Integer, nullable=False)
    build_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    definition_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    definition_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_branch: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    repository_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    repository_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    requested_for: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requested_for_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    queue_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finish_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
