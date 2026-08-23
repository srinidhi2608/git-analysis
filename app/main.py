from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, engine
from app.config_loader import load_active_repositories

active_repos: list = []


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
