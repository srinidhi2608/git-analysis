import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, TypeVar

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
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
    get_target_branches,
)
from app.ingest import run_ingestion, sync_developer_teams
from app.models import AzurePipelineRun, AzureWorkItem, Developer, PullRequest
from app.snapshot import list_snapshots, load_snapshot, resolve_snapshot, save_snapshot
from app.teams import load_team_config
from app.time_window import TimeWindow
from app.devops_config import load_devops_config
from app.devops_ingest import run_devops_ingestion
from app.devops_metrics import (
    StoryFilters,
    compute_dora,
    list_people,
    list_tags,
    markdown_report,
    unlinked_azure_identities,
    people_efficiency,
    query_stories,
    stories_csv,
    summarize_stories,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
active_repos: list = []
T = TypeVar("T")
# How often a long-running request checks whether the browser has gone away.
DISCONNECT_POLL_SECONDS = 0.5


def time_window(
    date_from: str | None = Query(
        None, description="Start of the range: ISO date or date-time (UTC unless an offset is given)"
    ),
    date_to: str | None = Query(None, description="End of the range (exclusive date-time, or a whole date). Default: now"),
    days: int | None = Query(None, ge=1, le=3650, description="Last N days, when date_from is not given (default 30)"),
) -> TimeWindow:
    """Date/time range shared by the dashboard endpoints (PRs created or merged inside it)."""
    try:
        return TimeWindow.parse(date_from, date_to, default_days=days or 30)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date range: {exc}") from exc


def _sync_range(date_from: datetime | None, date_to: datetime | None, lookback_days: int) -> TimeWindow:
    """Range for a sync request: explicit dates, else the last ``lookback_days`` days."""
    if lookback_days < 1:
        raise HTTPException(status_code=400, detail="lookback_days must be at least 1")
    try:
        return TimeWindow.parse(
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
            default_days=lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date range: {exc}") from exc


def target_branches(
    branches: list[str] = Query(
        default=[],
        description="Only PRs targeting these base branches; repeat for several (e.g. ?branches=main&branches=develop)",
    ),
) -> list[str] | None:
    """Optional target-branch filter shared by the PR-based endpoints."""
    return [branch for branch in branches if branch] or None


# Non-standard "Client Closed Request" status (nginx convention); the client never sees it.
CLIENT_CLOSED_REQUEST = 499
# Only one ingestion (or snapshot save/load) may run at a time; concurrent runs
# would race on upserts.
_ingestion_lock = threading.Lock()
# Outcome of the most recent sync, reported by GET /api/ingest/status.
_last_ingestion: dict | None = None
# Outcome of the most recent Azure DevOps sync, reported by GET /api/devops/sync/status.
_last_devops_sync: dict | None = None

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
    # Display name from config.yaml teams (the GitHub login if not configured).
    label: str | None = None
    cycleTimeHours: float


class PrsPerWeekItem(BaseModel):
    week: str
    mergedPrs: int


class DeveloperItem(BaseModel):
    github_username: str
    team_name: str | None
    # Display name from config.yaml teams (the GitHub login if not configured).
    display_name: str | None = None


class TeamMemberItem(BaseModel):
    key: str
    display_name: str
    github: str | None
    azure_devops: list[str]
    role: str | None


class TeamItem(BaseModel):
    name: str
    # Member keys (GitHub login, or display name for members without GitHub).
    members: list[str]
    people: list[TeamMemberItem] = []


class TeamsResponse(BaseModel):
    enabled: bool
    teams: list[TeamItem]


class IngestStatusResponse(BaseModel):
    status: str
    message: str
    last_run: dict | None = None


class IngestRequest(BaseModel):
    # Fetch PRs updated in [date_from, date_to); date_to defaults to now. Without
    # date_from, the last `lookback_days` days.
    date_from: datetime | None = None
    date_to: datetime | None = None
    lookback_days: int = 7
    # Re-download every PR in the range, even ones unchanged on GitHub.
    full_refresh: bool = False


class DevOpsSyncRequest(BaseModel):
    date_from: datetime | None = None
    date_to: datetime | None = None
    lookback_days: int = 30


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
    window_days: float
    window_start: str | None = None
    window_end: str | None = None
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


app = FastAPI(
    title="Git Analysis API",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

_cors_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Standard hardening headers; HSTS only over HTTPS (direct, or via a proxy's X-Forwarded-Proto)."""
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

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
        teams=[
            TeamItem(
                name=name,
                members=members,
                people=[TeamMemberItem(**member) for member in team_config.members() if member["team"] == name],
            )
            for name, members in team_config.teams.items()
        ],
    )


@app.get("/api/people")
def people_mapping(db: Session = Depends(get_db)):
    """Each configured person's GitHub and Azure DevOps identities and how much stored data matched them.

    Also lists Azure DevOps identities in the stored data that match nobody, to
    help fill in ``azure_devops`` in config.yaml.
    """
    team_config = load_team_config()
    pr_counts = dict(
        db.query(Developer.github_username, func.count(PullRequest.id))
        .join(PullRequest, PullRequest.developer_id == Developer.id)
        .group_by(Developer.github_username)
        .all()
    )
    pr_counts = {login.lower(): count for login, count in pr_counts.items()}
    work_items: dict[str, int] = {}
    for email, name in db.query(AzureWorkItem.assigned_to_email, AzureWorkItem.assigned_to).all():
        member = team_config.member_for_azure(email, name)
        if member:
            work_items[member.key] = work_items.get(member.key, 0) + 1
    people = [
        {
            **member,
            "pull_requests": pr_counts.get((member["github"] or "").lower(), 0),
            "work_items": work_items.get(member["key"], 0),
        }
        for member in team_config.members()
    ]
    return {
        "teams_enabled": team_config.enabled,
        "people": people,
        "unlinked_azure_identities": unlinked_azure_identities(db, team_config),
    }


@app.get("/active-repositories")
def get_active_repositories():
    return {"repositories": active_repos}


@app.get("/api/team-performance", response_model=list[TeamPerformanceResponse])
def team_performance(
    team: str | None = None,
    window: TimeWindow = Depends(time_window),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    return get_team_performance_metrics(db, window=window, teams=_resolve_teams(team), branches=branches)


@app.get("/api/dora/team", response_model=TeamDoraMetricsResponse)
def team_dora_metrics(
    team: str | None = None,
    window: TimeWindow = Depends(time_window),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    return get_team_dora_metrics(db, window=window, teams=_resolve_teams(team), branches=branches)


@app.get("/api/branches")
def list_target_branches(
    team: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int | None = Query(None, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """Target (base) branches of the stored PRs, with PR counts, for the branch filter.

    Without a range, every stored PR is counted.
    """
    window = time_window(date_from, date_to, days) if (date_from or date_to or days) else None
    return get_target_branches(db, teams=_resolve_teams(team), window=window)


@app.get("/api/developers", response_model=list[DeveloperItem])
def list_developers(team: str | None = None, db: Session = Depends(get_db)):
    team_config = load_team_config()
    return [
        {**row, "display_name": team_config.display_name(row["github_username"])}
        for row in get_all_developers(db, teams=_resolve_teams(team))
    ]


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
async def developer_analytics(
    github_username: str,
    request: Request,
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    try:
        analytics = await cancel_on_disconnect(
            request, get_developer_review_analytics(db, github_username, branches=branches)
        )
    except HTTPException as exc:
        if exc.status_code == CLIENT_CLOSED_REQUEST:
            logger.info("Analytics request for %s cancelled: the client disconnected.", github_username)
        raise
    if analytics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return analytics


@app.get("/api/developers/{github_username}/dora", response_model=DeveloperDoraMetricsResponse)
def developer_dora_metrics(
    github_username: str,
    window: TimeWindow = Depends(time_window),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    metrics = get_developer_dora_metrics(db, github_username, window=window, branches=branches)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics


@app.get("/api/chart/pr-cycle-by-developer", response_model=list[PrCycleByDeveloperItem])
def chart_pr_cycle_by_developer(
    team: str | None = None,
    window: TimeWindow = Depends(time_window),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    team_config = load_team_config()
    return [
        {**row, "label": team_config.display_name(row["developer"])}
        for row in get_pr_cycle_time_by_developer(db, window=window, teams=_resolve_teams(team), branches=branches)
    ]


@app.get("/api/chart/prs-per-week", response_model=list[PrsPerWeekItem])
def chart_prs_per_week(
    db: Session = Depends(get_db),
    developer: str | None = None,
    team: str | None = None,
    window: TimeWindow = Depends(time_window),
    branches: list[str] | None = Depends(target_branches),
):
    return get_prs_per_week(db, developer=developer, window=window, teams=_resolve_teams(team), branches=branches)


def _background_ingest(window: TimeWindow, full_refresh: bool = False):
    """Run ingestion in a background thread with its own DB session.

    The caller must hold ``_ingestion_lock``; it is released when the run ends.
    """
    global _last_ingestion
    db = SessionLocal()
    result: dict = {"range": window.describe()}
    try:
        stats = run_ingestion(db, since=window.start, until=window.end, full_refresh=full_refresh)
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
    """Trigger GitHub data ingestion into Postgres (runs in background).

    Fetches PRs updated in ``[date_from, date_to)`` (or the last ``lookback_days`` days).
    """
    window = _sync_range(payload.date_from, payload.date_to, payload.lookback_days)
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ingestion run is already in progress.")

    background_tasks.add_task(_background_ingest, window, payload.full_refresh)
    return IngestStatusResponse(
        status="accepted",
        message=f"Ingestion started in the background for PRs updated {_describe_range(window)}.",
    )


def _describe_range(window: TimeWindow) -> str:
    return f"between {window.start:%Y-%m-%d %H:%M} and {window.end:%Y-%m-%d %H:%M} UTC"


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
# DevOps Integration (Azure Boards + Azure Pipelines)
# ---------------------------------------------------------------------------


def _story_filters(
    date_from: str | None,
    date_to: str | None,
    tags: list[str],
    tag_mode: str,
    person: str | None,
    team: str | None,
    types: list[str],
    status: str,
) -> StoryFilters:
    try:
        window = TimeWindow.parse(date_from, date_to, default_days=30)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date range: {exc}") from exc
    if tag_mode not in ("any", "all"):
        raise HTTPException(status_code=400, detail="tag_mode must be 'any' or 'all'")
    if status not in ("completed", "all"):
        raise HTTPException(status_code=400, detail="status must be 'completed' or 'all'")
    if team:
        _resolve_teams(team)  # 404 for unknown teams
    return StoryFilters(
        date_from=window.start.date(),
        date_to=(window.end - timedelta(microseconds=1)).date(),
        window=window,
        tags=[tag for tag in tags if tag],
        tag_mode=tag_mode,
        person=person or None,
        team=team or None,
        types=[item for item in types if item],
        status=status,
    )


def story_filters(
    date_from: str | None = Query(None, description="Start: ISO date or date-time, UTC unless an offset is given (default: 30 days ago)"),
    date_to: str | None = Query(None, description="End: exclusive date-time, or a whole date (default: now)"),
    tags: list[str] = Query(default=[], description="Work item tags; repeat the parameter for several"),
    tag_mode: str = Query("any", description="'any' or 'all' of the tags"),
    person: str | None = Query(None, description="GitHub login of a configured member, or an Azure identity"),
    team: str | None = Query(None),
    types: list[str] = Query(default=[], description="Work item types, e.g. 'User Story'"),
    status: str = Query("completed", description="'completed' (closed in range) or 'all' (also open items)"),
) -> StoryFilters:
    return _story_filters(date_from, date_to, tags, tag_mode, person, team, types, status)


@app.get("/api/devops/config")
def devops_config(db: Session = Depends(get_db)):
    """Azure DevOps connection settings (without the token) and stored data counts."""
    config = load_devops_config()
    return {
        "configured": config.configured,
        "missing": config.missing(),
        "organization": config.organization_url,
        "projects": config.projects,
        "work_item_types": config.work_item_types,
        "deployment_pattern": config.deployment_pattern,
        "production_pattern": config.production_pattern,
        "incidents_enabled": config.incidents_enabled,
        "work_items": db.query(AzureWorkItem.id).count(),
        "pipeline_runs": db.query(AzurePipelineRun.id).count(),
        "last_sync": _last_devops_sync,
    }


def _background_devops_sync(window: TimeWindow) -> None:
    """Run the Azure DevOps sync; the caller holds ``_ingestion_lock``."""
    global _last_devops_sync
    db = SessionLocal()
    result: dict = {"range": window.describe()}
    try:
        result.update(run_devops_ingestion(db, since=window.start, until=window.end).as_dict())
        if settings.snapshot_after_sync:
            try:
                result["snapshot"] = save_snapshot(db, settings.snapshot_dir)["name"]
            except Exception:
                logger.exception("Could not save a snapshot after the Azure DevOps sync.")
    except Exception as exc:
        logger.exception("Azure DevOps sync failed.")
        result["error"] = str(exc)
    finally:
        db.close()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _last_devops_sync = result
        _ingestion_lock.release()


@app.post("/api/devops/sync", response_model=IngestStatusResponse, status_code=202)
def devops_sync(
    background_tasks: BackgroundTasks,
    payload: DevOpsSyncRequest = Body(default_factory=DevOpsSyncRequest),
):
    """Fetch Azure Boards work items and Azure Pipelines runs changed in ``[date_from, date_to)``."""
    config = load_devops_config()
    if not config.configured:
        raise HTTPException(status_code=400, detail="Azure DevOps is not configured: missing " + ", ".join(config.missing()))
    window = _sync_range(payload.date_from, payload.date_to, payload.lookback_days)
    if not _ingestion_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A sync is already in progress.")
    background_tasks.add_task(_background_devops_sync, window)
    return IngestStatusResponse(
        status="accepted",
        message=f"Azure DevOps sync started for items changed {_describe_range(window)}.",
    )


@app.get("/api/devops/sync/status", response_model=IngestStatusResponse)
def devops_sync_status():
    status = "running" if _ingestion_lock.locked() else "idle"
    return IngestStatusResponse(status=status, message=f"Sync is {status}.", last_run=_last_devops_sync)


@app.get("/api/devops/filters")
def devops_filter_options(db: Session = Depends(get_db)):
    """Values for the DevOps page filters: tags, people, work item types, teams."""
    team_config = load_team_config()
    types = [value for (value,) in db.query(AzureWorkItem.work_item_type).distinct().order_by(AzureWorkItem.work_item_type)]
    return {
        "tags": list_tags(db),
        "people": list_people(db, team_config),
        "unlinked_identities": unlinked_azure_identities(db, team_config),
        "types": types,
        "teams": team_config.team_names,
    }


@app.get("/api/devops/stories")
def devops_stories(filters: StoryFilters = Depends(story_filters), db: Session = Depends(get_db)):
    """Filtered work items with a summary and per-person efficiency."""
    team_config = load_team_config()
    stories = query_stories(db, filters, team_config)
    return {
        "filters": filters.describe(),
        "summary": summarize_stories(stories, filters),
        "efficiency": people_efficiency(db, stories, filters, team_config),
        "stories": stories,
    }


@app.get("/api/devops/dora")
def devops_dora(
    filters: StoryFilters = Depends(story_filters),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    """DORA metrics (DevLake definitions) for everyone, a team, or one person."""
    return compute_dora(
        db,
        load_devops_config(),
        load_team_config(),
        filters.date_from,
        filters.date_to,
        person=filters.person,
        team=filters.team,
        branches=branches,
        window=filters.bounds,
    )


@app.get("/api/devops/report")
def devops_report(
    format: str = Query("markdown", description="'markdown' or 'csv'"),
    filters: StoryFilters = Depends(story_filters),
    branches: list[str] | None = Depends(target_branches),
    db: Session = Depends(get_db),
):
    """Download a report of the filtered work items (CSV, or Markdown with summary, DORA and efficiency)."""
    team_config = load_team_config()
    stories = query_stories(db, filters, team_config)
    stamp = f"{filters.date_from:%Y%m%d}-{filters.date_to:%Y%m%d}"
    if format == "csv":
        return Response(
            stories_csv(stories),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="devops-stories-{stamp}.csv"'},
        )
    if format != "markdown":
        raise HTTPException(status_code=400, detail="format must be 'markdown' or 'csv'")
    dora = compute_dora(
        db,
        load_devops_config(),
        team_config,
        filters.date_from,
        filters.date_to,
        person=filters.person,
        team=filters.team,
        branches=branches,
        window=filters.bounds,
    )
    report = markdown_report(
        filters,
        summarize_stories(stories, filters),
        people_efficiency(db, stories, filters, team_config),
        stories,
        dora,
        person_label=team_config.display_name(filters.person) if filters.person else None,
    )
    return Response(
        report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="devops-report-{stamp}.md"'},
    )


# ---------------------------------------------------------------------------
# Serve the React SPA (production build embedded in the Docker image)
# ---------------------------------------------------------------------------
# The frontend is built into frontend/dist during the Docker image build.
# In development the Vite dev server handles the UI; in production FastAPI
# serves the built assets so no separate web server is needed.

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
API_DOC_PATHS = {"docs", "redoc", "openapi.json", "docs/oauth2-redirect"}

if _FRONTEND_DIST.is_dir():
    # Serve JS/CSS/image assets under /assets
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """Return index.html for any path not matched by an API route."""
        if full_path == "api" or full_path.startswith("api/") or full_path in API_DOC_PATHS:
            # Unknown API routes, and the API docs when ENABLE_API_DOCS=false.
            raise HTTPException(status_code=404, detail="Not Found")
        # Serve real files from the build root (favicon.svg, icons.svg, ...).
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST.resolve()):
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
