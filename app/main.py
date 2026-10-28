import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, TypeVar

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
from app.ingest import run_ingestion, sync_developer_teams
from app.models import PullRequest
from app.snapshot import list_snapshots, load_snapshot, resolve_snapshot, save_snapshot
from app.teams import load_team_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
active_repos: list = []
T = TypeVar("T")
# How often a long-running request checks whether the browser has gone away.
DISCONNECT_POLL_SECONDS = 0.5
# Dashboard window ("Lookback" in the UI), in days.
WindowDays = Query(30, ge=1, le=3650, description="Only include PRs created or merged in the last N days")
# Non-standard "Client Closed Request" status (nginx convention); the client never sees it.
CLIENT_CLOSED_REQUEST = 499
# Only one ingestion (or snapshot save/load) may run at a time; concurrent runs
# would race on upserts.
_ingestion_lock = threading.Lock()
# Outcome of the most recent sync, reported by GET /api/ingest/status.
_last_ingestion: dict | None = None

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


class TeamItem(BaseModel):
    name: str
    members: list[str]


class TeamsResponse(BaseModel):
    enabled: bool
    teams: list[TeamItem]


class IngestStatusResponse(BaseModel):
    status: str
    message: str
    last_run: dict | None = None


class IngestRequest(BaseModel):
    lookback_days: int = 7
    # Re-download every PR in the lookback window, even ones unchanged on GitHub.
    full_refresh: bool = False


class SnapshotItem(BaseModel):
    name: str
    size_bytes: int
    created_at: str


class SnapshotListResponse(BaseModel):
    directory: str
    autoload: bool
    save_after_sync: bool
    snapshots: list[SnapshotItem]


class SnapshotResultResponse(BaseModel):
    name: str
    counts: dict[str, int]


class SnapshotLoadRequest(BaseModel):
    # Snapshot file name; the newest snapshot when omitted.
    name: str | None = None


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
    # AI tier that analysed the review comments, e.g. "Ollama (qwen2.5-coder:14b)".
    provider: str
    # Engine that wrote the overview text ("heuristic-fallback" or "configured-http-provider").
    narrative_provider: str = ""
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
    ghost_merge_count: int = 0
    ghost_merge_rate: float = 0.0


class DoraWeeklyTrendItem(BaseModel):
    week: str
    merged_prs: int
    average_lead_time_hours: float | None
    average_time_to_first_review_hours: float | None
    change_failure_proxy_rate: float
    ghost_merges: int = 0


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


def _autoload_snapshot(db: Session) -> None:
    """Restore the newest snapshot into an empty database (e.g. after `down -v`)."""
    if not settings.snapshot_autoload or db.query(PullRequest.id).first() is not None:
        return
    path = resolve_snapshot(settings.snapshot_dir)
    if path is None:
        return
    try:
        load_snapshot(db, path)
    except Exception:
        logger.exception("Could not load snapshot %s at startup.", path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_database_schema()
    global active_repos
    active_repos = load_active_repositories()
    db = SessionLocal()
    try:
        _autoload_snapshot(db)
        sync_developer_teams(db, load_team_config())
    finally:
        db.close()
    yield


app = FastAPI(title="Git Analysis API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
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
        "confidence_high_prs": settings.confidence_high_prs,
        "confidence_high_comments": settings.confidence_high_comments,
    }


def _resolve_teams(team: str | None) -> list[str] | None:
    """Translate the optional ``?team=`` query parameter into a team filter.

    Returns ``None`` (no filtering) when no teams are configured. config.yaml is
    re-read on every call so team edits apply without a restart.
    """
    team_config = load_team_config()
    if team:
        if team_config.enabled and team not in team_config.teams:
            raise HTTPException(status_code=404, detail=f"Team '{team}' is not configured")
        return [team]
    if team_config.enabled:
        return team_config.team_names
    return None


@app.get("/api/teams", response_model=TeamsResponse)
def list_teams():
    """Teams and members configured in config.yaml."""
    team_config = load_team_config()
    return TeamsResponse(
        enabled=team_config.enabled,
        teams=[TeamItem(name=name, members=members) for name, members in team_config.teams.items()],
    )


@app.get("/active-repositories")
def get_active_repositories():
    return {"repositories": active_repos}


@app.get("/api/team-performance", response_model=list[TeamPerformanceResponse])
def team_performance(team: str | None = None, days: int = WindowDays, db: Session = Depends(get_db)):
    return get_team_performance_metrics(db, days=days, teams=_resolve_teams(team))


@app.get("/api/dora/team", response_model=TeamDoraMetricsResponse)
def team_dora_metrics(team: str | None = None, days: int = WindowDays, db: Session = Depends(get_db)):
    return get_team_dora_metrics(db, window_days=days, teams=_resolve_teams(team))


@app.get("/api/developers", response_model=list[DeveloperItem])
def list_developers(team: str | None = None, db: Session = Depends(get_db)):
    return get_all_developers(db, teams=_resolve_teams(team))


@app.get("/api/developers/{github_username}", response_model=DeveloperMetricsResponse)
def developer_metrics(github_username: str, db: Session = Depends(get_db)):
    metrics = get_developer_metrics(db, github_username)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics


async def cancel_on_disconnect(request: Request, work: Awaitable[T]) -> T:
    """Await ``work``, cancelling it if the client disconnects first.

    The dashboard aborts the analytics request when the user picks another
    developer. Cancelling here closes the in-flight LLM HTTP call too, so an
    abandoned analysis does not keep the LLM (e.g. Ollama, which generates one
    response at a time) busy ahead of the developer that was selected next.
    """
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise HTTPException(status_code=CLIENT_CLOSED_REQUEST, detail="Client closed request")
    finally:
        if not task.done():
            task.cancel()


@app.get("/api/developers/{github_username}/analytics", response_model=DeveloperAnalyticsResponse)
async def developer_analytics(github_username: str, request: Request, db: Session = Depends(get_db)):
    try:
        analytics = await cancel_on_disconnect(request, get_developer_review_analytics(db, github_username))
    except HTTPException as exc:
        if exc.status_code == CLIENT_CLOSED_REQUEST:
            logger.info("Analytics request for %s cancelled: the client disconnected.", github_username)
        raise
    if analytics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return analytics


@app.get("/api/developers/{github_username}/dora", response_model=DeveloperDoraMetricsResponse)
def developer_dora_metrics(github_username: str, days: int = WindowDays, db: Session = Depends(get_db)):
    metrics = get_developer_dora_metrics(db, github_username, window_days=days)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics


@app.get("/api/chart/pr-cycle-by-developer", response_model=list[PrCycleByDeveloperItem])
def chart_pr_cycle_by_developer(team: str | None = None, days: int = WindowDays, db: Session = Depends(get_db)):
    return get_pr_cycle_time_by_developer(db, days=days, teams=_resolve_teams(team))


@app.get("/api/chart/prs-per-week", response_model=list[PrsPerWeekItem])
def chart_prs_per_week(
    db: Session = Depends(get_db),
    developer: str | None = None,
    team: str | None = None,
    days: int = WindowDays,
):
    return get_prs_per_week(db, developer=developer, days=days, teams=_resolve_teams(team))


def _background_ingest(lookback_days: int = 7, full_refresh: bool = False):
    """Run ingestion in a background thread with its own DB session.

    The caller must hold ``_ingestion_lock``; it is released when the run ends.
    """
    global _last_ingestion
    db = SessionLocal()
    result: dict = {"lookback_days": lookback_days}
    try:
        stats = run_ingestion(db, lookback_days=lookback_days, full_refresh=full_refresh)
        result.update(stats.as_dict())
        if settings.snapshot_after_sync:
            try:
                result["snapshot"] = save_snapshot(db, settings.snapshot_dir)["name"]
            except Exception:
                logger.exception("Could not save a snapshot after the sync.")
    except Exception as exc:
        logger.exception("Background ingestion failed.")
        result["error"] = str(exc)
    finally:
        db.close()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _last_ingestion = result
        _ingestion_lock.release()


@app.get("/api/ingest/status", response_model=IngestStatusResponse)
def ingest_status():
    """Report whether a GitHub ingestion run is currently in progress."""
    if _ingestion_lock.locked():
        return IngestStatusResponse(status="running", message="Ingestion is in progress.", last_run=_last_ingestion)
    return IngestStatusResponse(status="idle", message="No ingestion is running.", last_run=_last_ingestion)


@app.post("/api/ingest", response_model=IngestStatusResponse, status_code=202)
def trigger_ingest(
    background_tasks: BackgroundTasks,
    payload: IngestRequest = Body(default_factory=IngestRequest),
):
    """Trigger GitHub data ingestion into Postgres (runs in background)."""
    if payload.lookback_days < 1:
        raise HTTPException(status_code=400, detail="lookback_days must be at least 1")
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ingestion run is already in progress.")

    background_tasks.add_task(_background_ingest, payload.lookback_days, payload.full_refresh)
    return IngestStatusResponse(
        status="accepted",
        message=f"Ingestion started in the background for PRs updated in the last {payload.lookback_days} days.",
    )


@app.get("/api/snapshots", response_model=SnapshotListResponse)
def snapshots():
    """Saved snapshots of the ingested data, newest first."""
    return SnapshotListResponse(
        directory=str(Path(settings.snapshot_dir).resolve()),
        autoload=settings.snapshot_autoload,
        save_after_sync=settings.snapshot_after_sync,
        snapshots=[SnapshotItem(**item) for item in list_snapshots(settings.snapshot_dir)],
    )


@app.post("/api/snapshots", response_model=SnapshotResultResponse)
def create_snapshot(db: Session = Depends(get_db)):
    """Save the current data as a new snapshot file."""
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A sync is running; save the snapshot when it finishes.")
    try:
        return save_snapshot(db, settings.snapshot_dir)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write snapshot: {exc}") from exc
    finally:
        _ingestion_lock.release()


@app.post("/api/snapshots/load", response_model=SnapshotResultResponse)
def restore_snapshot(
    payload: SnapshotLoadRequest = Body(default_factory=SnapshotLoadRequest),
    db: Session = Depends(get_db),
):
    """Upsert a snapshot (the newest by default) into the database."""
    try:
        path = resolve_snapshot(settings.snapshot_dir, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="No snapshot found.")
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A sync is running; load the snapshot when it finishes.")
    try:
        counts = load_snapshot(db, path)
        sync_developer_teams(db, load_team_config())
        return SnapshotResultResponse(name=path.name, counts=counts)
    finally:
        _ingestion_lock.release()


# ---------------------------------------------------------------------------
# Serve the React SPA (production build embedded in the Docker image)
# ---------------------------------------------------------------------------
# The frontend is built into frontend/dist during the Docker image build.
# In development the Vite dev server handles the UI; in production FastAPI
# serves the built assets so no separate web server is needed.

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    # Serve JS/CSS/image assets under /assets
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """Return index.html for any path not matched by an API route."""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # Serve real files from the build root (favicon.svg, icons.svg, ...).
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST.resolve()):
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
