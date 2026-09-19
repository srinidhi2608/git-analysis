from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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

    repository: Mapped["Repository"] = relationship("Repository", back_populates="pull_requests")
    developer: Mapped["Developer"] = relationship("Developer", back_populates="pull_requests")
    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="pull_request")
    comments: Mapped[list["PullRequestComment"]] = relationship("PullRequestComment", back_populates="pull_request")


class Commit(Base):
    __tablename__ = "commits"

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
