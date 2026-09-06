import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db
from app.config_loader import load_active_repositories
from app.github_ingestion import GitHubIngestionService, RateLimitError
from app.queries import (
    get_developer_metrics,
    get_team_performance_metrics,
    get_pr_cycle_time_by_developer,
    get_prs_per_week,
    get_all_developers,
)
from app.ingest import run_ingestion

logger = logging.getLogger(__name__)

active_repos: list = []
_pr_cache: dict[int, tuple[datetime, list[dict[str, Any]]]] = {}
_pr_cache_lock = Lock()
_pr_cache_inflight: dict[int, Event] = {}
_PR_CACHE_TTL = timedelta(seconds=60)

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class TeamPerformanceResponse(BaseModel):
    team_name: str
    average_pr_cycle_time_minutes: float | None
    total_commits: int
    total_prs: int


class DeveloperMetricsResponse(BaseModel):
    github_username: str
    team_name: str | None
    average_pr_cycle_time_minutes: float | None
    rework_count: int
    comments_received: int


class PrCycleByDeveloperItem(BaseModel):
    developer: str
    cycleTimeHours: float


class PrsPerWeekItem(BaseModel):
    week: str
    mergedPrs: int


class DeveloperItem(BaseModel):
    github_username: str
    team_name: str | None


class PullRequestActor(BaseModel):
    login: str


class PullRequestCountMetric(BaseModel):
    totalCount: int


class PullRequestGraphItem(BaseModel):
    number: int
    title: str
    createdAt: str | None
    mergedAt: str | None
    closedAt: str | None
    author: PullRequestActor
    reviews: PullRequestCountMetric
    comments: PullRequestCountMetric
    commits: PullRequestCountMetric
    reviewDecision: str | None


class IngestStatusResponse(BaseModel):
    status: str
    message: str


class IngestRequest(BaseModel):
    lookback_days: int = 7


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    global active_repos
    active_repos = load_active_repositories()
    yield


app = FastAPI(title="Git Analysis API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"message": "Git Analysis API is running"}


@app.get("/active-repositories")
def get_active_repositories():
    return {"repositories": active_repos}


@app.get("/api/team-performance", response_model=list[TeamPerformanceResponse])
def team_performance(db: Session = Depends(get_db)):
    return get_team_performance_metrics(db)


@app.get("/api/developers", response_model=list[DeveloperItem])
def list_developers(db: Session = Depends(get_db)):
    return get_all_developers(db)


@app.get("/api/developers/{github_username}", response_model=DeveloperMetricsResponse)
def developer_metrics(github_username: str, db: Session = Depends(get_db)):
    metrics = get_developer_metrics(db, github_username)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics


@app.get("/api/chart/pr-cycle-by-developer", response_model=list[PrCycleByDeveloperItem])
def chart_pr_cycle_by_developer(db: Session = Depends(get_db)):
    return get_pr_cycle_time_by_developer(db)


@app.get("/api/chart/prs-per-week", response_model=list[PrsPerWeekItem])
def chart_prs_per_week(db: Session = Depends(get_db), developer: str | None = None):
    return get_prs_per_week(db, developer=developer)


@app.get("/api/github/pull-requests", response_model=list[PullRequestGraphItem])
def list_pull_requests_for_graphs(lookback_days: int = 7):
    if lookback_days < 1:
        raise HTTPException(status_code=400, detail="lookback_days must be at least 1")
    try:
        pull_requests: list[dict[str, Any]] | None = None
        while pull_requests is None:
            should_fetch = False
            wait_event: Event | None = None
            now = datetime.now(timezone.utc)
            with _pr_cache_lock:
                cached = _pr_cache.get(lookback_days)
                if cached and now - cached[0] <= _PR_CACHE_TTL:
                    pull_requests = cached[1]
                    break

                inflight_event = _pr_cache_inflight.get(lookback_days)
                if inflight_event is None:
                    wait_event = Event()
                    _pr_cache_inflight[lookback_days] = wait_event
                    should_fetch = True
                else:
                    wait_event = inflight_event

            if should_fetch and wait_event is not None:
                fetched_pull_requests: list[dict[str, Any]] | None = None
                try:
                    service = GitHubIngestionService(lookback_days=lookback_days)
                    fetched_pull_requests = service.fetch_all()
                finally:
                    with _pr_cache_lock:
                        if fetched_pull_requests is not None:
                            _pr_cache[lookback_days] = (datetime.now(timezone.utc), fetched_pull_requests)
                        _pr_cache_inflight.pop(lookback_days, None)
                        wait_event.set()

                pull_requests = fetched_pull_requests
            elif wait_event is not None:
                wait_event.wait(timeout=30)
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to fetch pull requests from GitHub.")
        raise HTTPException(status_code=502, detail="Failed to fetch pull requests from GitHub.") from exc

    return [
        {
            "number": pr["pr_number"],
            "title": pr.get("title", ""),
            "createdAt": pr.get("created_at"),
            "mergedAt": pr.get("merged_at"),
            "closedAt": pr.get("closed_at"),
            "author": {"login": pr.get("author") or "unknown"},
            "reviews": {"totalCount": int(pr.get("reviews_total_count", 0) or 0)},
            "comments": {"totalCount": int(pr.get("comments_total_count", 0) or 0)},
            "commits": {"totalCount": int(pr.get("commit_count", 0) or 0)},
            "reviewDecision": pr.get("review_decision"),
        }
        for pr in pull_requests
        if pr.get("pr_number") is not None
    ]


def _background_ingest(lookback_days: int = 7):
    """Run ingestion in a background thread with its own DB session."""
    db = SessionLocal()
    try:
        run_ingestion(db, lookback_days=lookback_days)
    except Exception:
        logger.exception("Background ingestion failed.")
    finally:
        db.close()


@app.post("/api/ingest", response_model=IngestStatusResponse)
def trigger_ingest(
    background_tasks: BackgroundTasks,
    payload: IngestRequest = Body(default_factory=IngestRequest),
):
    """Trigger GitHub data ingestion into Postgres (runs in background)."""
    if payload.lookback_days < 1:
        raise HTTPException(status_code=400, detail="lookback_days must be at least 1")

    background_tasks.add_task(_background_ingest, payload.lookback_days)
    return IngestStatusResponse(
        status="accepted",
        message=f"Ingestion started in the background for the last {payload.lookback_days} days. Refresh data in a few seconds.",
    )
