import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, ensure_database_schema, get_db
from app.config import settings
from app.config_loader import load_active_repositories
from app.developer_analytics import get_developer_review_analytics
from app.dora_metrics import get_developer_dora_metrics, get_team_dora_metrics
from app.queries import (
    get_developer_metrics,
    get_team_performance_metrics,
    get_pr_cycle_time_by_developer,
    get_prs_per_week,
    get_all_developers,
)
from app.ingest import run_ingestion

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
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


class DeveloperAnalyticsMetricsResponse(BaseModel):
    pull_request_count: int
    merged_pull_request_count: int
    total_review_comments: int
    average_comments_per_pr: float
    average_pr_size: float
    average_changed_files: float
    largest_pr_size: int
    comment_density_per_100_lines: float
    requested_changes_rate: float
    average_rework_commits_per_pr: float
    average_cycle_time_hours: float | None
    average_time_to_first_followup_hours: float | None
    average_time_to_merge_after_feedback_hours: float | None
    large_pr_rate: float


class CommentCategoryItem(BaseModel):
    category: str
    count: int


class RepeatedIssueCategoryItem(BaseModel):
    category: str
    count: int
    pull_request_count: int


class DeveloperReviewPullRequestItem(BaseModel):
    pr_number: int
    title: str
    created_at: str | None
    merged_at: str | None
    review_comments: int
    requested_changes: int
    rework_commits: int
    size: int


class ReviewerItem(BaseModel):
    login: str
    comment_count: int


class DeveloperAnalyticsBreakdownResponse(BaseModel):
    comment_categories: list[CommentCategoryItem]
    repeated_issue_categories: list[RepeatedIssueCategoryItem]
    pull_requests: list[DeveloperReviewPullRequestItem]
    reviewers: list[ReviewerItem] = []


class DeveloperAnalyticsCategorySummary(BaseModel):
    key: str
    label: str
    score: int | None
    assessment: str
    evidence: list[str]


class DeveloperAnalyticsSummaryResponse(BaseModel):
    provider: str
    confidence: str
    overview: str
    categories: list[DeveloperAnalyticsCategorySummary]
    highlights: list[str]
    risks: list[str]
    recommendations: list[str]
    strengths: list[str] = []
    improvement_areas: list[str] = []
    coding_standards_score: float | None = None
    design_patterns_summary: str = ""
    dry_vs_wet_observations: str = ""
    reviewer_rigor_score: float | None = None


class DeveloperAnalyticsSampleResponse(BaseModel):
    pull_requests: int
    comment_text_items: int
    followup_samples: int
    resolution_samples: int


class DeveloperAnalyticsResponse(BaseModel):
    github_username: str
    team_name: str | None
    languages: list[str] = []
    metrics: DeveloperAnalyticsMetricsResponse
    breakdown: DeveloperAnalyticsBreakdownResponse
    sample: DeveloperAnalyticsSampleResponse
    summary: DeveloperAnalyticsSummaryResponse


class DoraSummaryResponse(BaseModel):
    window_days: int
    pull_request_count: int
    merged_pull_request_count: int
    reviewed_pull_request_count: int
    merge_frequency_per_week: float
    average_lead_time_hours: float | None
    median_lead_time_hours: float | None
    average_time_to_first_review_hours: float | None
    review_coverage_rate: float
    approval_rate: float
    change_failure_proxy_rate: float
    average_recovery_time_hours: float | None
    recovery_samples: int


class DoraWeeklyTrendItem(BaseModel):
    week: str
    merged_prs: int
    average_lead_time_hours: float | None
    average_time_to_first_review_hours: float | None
    change_failure_proxy_rate: float


class TeamDoraMetricsResponse(BaseModel):
    summary: DoraSummaryResponse
    weekly_trends: list[DoraWeeklyTrendItem]


class DeveloperDoraMetricsResponse(BaseModel):
    github_username: str
    team_name: str | None
    summary: DoraSummaryResponse
    weekly_trends: list[DoraWeeklyTrendItem]


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_database_schema()
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


@app.get("/api/config/thresholds")
def get_thresholds():
    """Return configurable metric thresholds used by the dashboard."""
    return {
        "large_pr_threshold_lines": settings.large_pr_threshold_lines,
        "followup_good_threshold_hours": settings.followup_good_threshold_hours,
        "requested_changes_risky_pct": settings.requested_changes_risky_pct,
        "high_comments_per_pr_threshold": settings.high_comments_per_pr_threshold,
        "lead_time_healthy_hours": settings.lead_time_healthy_hours,
        "first_review_healthy_hours": settings.first_review_healthy_hours,
        "review_coverage_good_pct": settings.review_coverage_good_pct,
        "approval_rate_good_pct": settings.approval_rate_good_pct,
        "change_failure_acceptable_pct": settings.change_failure_acceptable_pct,
        "confidence_min_prs": settings.confidence_min_prs,
        "confidence_min_comments": settings.confidence_min_comments,
    }


@app.get("/active-repositories")
def get_active_repositories():
    return {"repositories": active_repos}


@app.get("/api/team-performance", response_model=list[TeamPerformanceResponse])
def team_performance(db: Session = Depends(get_db)):
    return get_team_performance_metrics(db)


@app.get("/api/dora/team", response_model=TeamDoraMetricsResponse)
def team_dora_metrics(db: Session = Depends(get_db)):
    return get_team_dora_metrics(db)


@app.get("/api/developers", response_model=list[DeveloperItem])
def list_developers(db: Session = Depends(get_db)):
    return get_all_developers(db)


@app.get("/api/developers/{github_username}", response_model=DeveloperMetricsResponse)
def developer_metrics(github_username: str, db: Session = Depends(get_db)):
    metrics = get_developer_metrics(db, github_username)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics


@app.get("/api/developers/{github_username}/analytics", response_model=DeveloperAnalyticsResponse)
async def developer_analytics(github_username: str, db: Session = Depends(get_db)):
    analytics = await get_developer_review_analytics(db, github_username)
    if analytics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return analytics


@app.get("/api/developers/{github_username}/dora", response_model=DeveloperDoraMetricsResponse)
def developer_dora_metrics(github_username: str, db: Session = Depends(get_db)):
    metrics = get_developer_dora_metrics(db, github_username)
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
