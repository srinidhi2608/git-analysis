from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.config_loader import load_active_repositories
from app.queries import get_developer_metrics, get_team_performance_metrics

active_repos: list = []


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    global active_repos
    active_repos = load_active_repositories()
    yield


app = FastAPI(title="Git Analysis API", lifespan=lifespan)


@app.get("/")
def root():
    return {"message": "Git Analysis API is running"}


@app.get("/active-repositories")
def get_active_repositories():
    return {"repositories": active_repos}


@app.get("/api/team-performance", response_model=list[TeamPerformanceResponse])
def team_performance(db: Session = Depends(get_db)):
    return get_team_performance_metrics(db)


@app.get("/api/developers/{github_username}", response_model=DeveloperMetricsResponse)
def developer_metrics(github_username: str, db: Session = Depends(get_db)):
    metrics = get_developer_metrics(db, github_username)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Developer not found")
    return metrics
