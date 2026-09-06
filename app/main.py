import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, get_db
from app.config_loader import load_active_repositories
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
