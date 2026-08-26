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

    repository: Mapped["Repository"] = relationship("Repository", back_populates="pull_requests")
    developer: Mapped["Developer"] = relationship("Developer", back_populates="pull_requests")
    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="pull_request")


class Commit(Base):
    __tablename__ = "commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pr_id: Mapped[int] = mapped_column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    developer_id: Mapped[int] = mapped_column(Integer, ForeignKey("developers.id"), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    pull_request: Mapped["PullRequest"] = relationship("PullRequest", back_populates="commits")
    developer: Mapped["Developer"] = relationship("Developer", back_populates="commits")
