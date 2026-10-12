# Git Analysis

**Git Analysis** is a self-hosted engineering-metrics dashboard built from GitHub pull-request data.

It pulls PRs, reviews, review comments and commits for the repositories you choose from the GitHub GraphQL API, stores them in PostgreSQL, and shows them in a web dashboard:

- **Team overview**: for all teams or one selected team, PR cycle time, merged PRs per week, and **DORA-inspired delivery metrics** (merge frequency, lead time, time to first review, review coverage, approval rate, change-failure proxy, recovery time) with weekly trends.
- **Individual performance**: for each developer, PR size, review-comment density, requested-changes rate, rework after feedback, follow-up speed, recurring review themes, top reviewers and the languages they work in.
- **AI summary (optional)**: written strengths, improvement areas, design-pattern and DRY/WET notes. These come from an online LLM (OpenAI or Gemini) or a local [Ollama](https://ollama.com) model, based on the actual review comments. A rule-based fallback always works, so no AI setup is required.

> These metrics are signals to start a conversation, not a performance verdict. The AI and heuristic scores come from review comments and PR metadata only. They don't look at the code itself.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Quick start with Docker Compose (recommended)](#2-quick-start-with-docker-compose-recommended)
3. [Configuration](#3-configuration)
4. [Getting data into the dashboard](#4-getting-data-into-the-dashboard)
5. [Running the API and UI separately (without Docker)](#5-running-the-api-and-ui-separately-without-docker)
6. [AI analysis (optional)](#6-ai-analysis-optional)
7. [API reference](#7-api-reference)
8. [What the metrics mean](#8-what-the-metrics-mean)
9. [Project layout](#9-project-layout)
10. [Running the tests](#10-running-the-tests)
11. [Troubleshooting](#11-troubleshooting)

---

## 1) How it works

```
config.yaml ──► GitHub GraphQL API ──► ingestion (POST /api/ingest) ──► PostgreSQL
(repos)                                                                     │
                                                                            ▼
 Browser ◄── React dashboard ◄── FastAPI (/api/*) ◄── metrics, DORA proxies, developer analytics
                                                        └─► AI summary: Online LLM → Ollama → rule-based
```

| Component | Technology | Where |
|---|---|---|
| API / backend | Python 3.11+, FastAPI, SQLAlchemy | `app/` |
| Dashboard UI | React 19, TypeScript, Vite, Tailwind, Recharts | `frontend/` |
| Database | PostgreSQL 16 | Docker volume `postgres_data` |
| AI (optional) | OpenAI / Gemini API, or a local Ollama model | `app/ai_analyzers.py` |

In the Docker setup, one container serves **both the API and the built UI on port 8000**. During local development the UI runs on the Vite dev server (port 5173), which forwards `/api/*` requests to the backend.

Database tables are created automatically when the backend starts. No migration step is needed.

---

## 2) Quick start with Docker Compose (recommended)

You only need **Docker** (Docker Desktop, or Docker Engine 24+ with the Compose v2 plugin) and a **GitHub Personal Access Token**. You don't need Python, Node.js or PostgreSQL on your machine.

### Step 1: Get the code

```bash
git clone https://github.com/srinidhi2608/git-analysis.git
cd git-analysis
```

### Step 2: Create a GitHub token

Create a token at <https://github.com/settings/tokens> with read access to the repositories you want to analyse:

- **Classic token**: the `repo` scope (or `public_repo` if you only analyse public repositories).
- **Fine-grained token**: repository permissions **Pull requests: Read** and **Contents: Read**.

### Step 3: Create your `.env`

```bash
cp .env.example .env
```

Open `.env` and set your token:

```env
GITHUB_TOKEN=ghp_your_token_here
```

That's the only required change. Optionally change `POSTGRES_PASSWORD` too, but do it **before** the first start, because the database password is fixed when the database volume is first created.

### Step 4: Choose the repositories (and optionally teams) to analyse

Edit `config.yaml`:

```yaml
repositories:
  - name: "your-org/your-repo"     # owner/repo format
    is_active: true
  - name: "your-org/another-repo"
    is_active: true

# Optional: only analyse these people, grouped into teams (see 3.3)
teams:
  GrowthTeam:
    - name: "CF-Vaishnavi"         # GitHub login
    - name: "CF-SinchanaBhat"
  IppPayFac:
    - name: "CF-NewMember"
```

### Step 5: Build and start

```bash
docker compose up --build
```

This will:

1. Build the React UI (Node 22) and the Python backend (Python 3.12) into one image.
2. Start PostgreSQL 16 with a persistent volume.
3. Start the app once the database is healthy, and create the tables.

The first build takes a few minutes. When you see `Uvicorn running on http://0.0.0.0:8000`, open:

| What | URL |
|---|---|
| **Dashboard (UI)** | <http://localhost:8000> |
| **Interactive API docs (Swagger)** | <http://localhost:8000/docs> |
| Health check | <http://localhost:8000/health> |

### Step 6: Load data

The dashboard starts empty. Click **⟳ Sync from GitHub** in the header. The button stays in "Syncing…" until ingestion finishes, then the dashboard reloads. Use the **Lookback** field to choose how many days of PR activity to import (for example 30 or 90 for a first import).

Just want to look around? [Load the sample data](#43-load-sample-demo-data) instead.

### Everyday commands

| Goal | Command |
|---|---|
| Start in the background | `docker compose up -d` |
| Follow the app logs | `docker compose logs -f app` |
| Stop | `docker compose down` |
| Stop **and delete all data** | `docker compose down -v` |
| Rebuild after pulling new code | `docker compose up --build -d` |
| Apply `config.yaml` changes | `docker compose restart app` |
| Open a database shell | `docker compose exec db psql -U git_user -d git_analysis` |

Ports can be changed in `.env` with `APP_PORT` (default `8000`) and `POSTGRES_PORT` (default `5432`) if they are already in use on your machine.

---

## 3) Configuration

### 3.1 `.env`

All settings are environment variables. The backend reads them from `.env` in the project root (when run directly) or from the container environment (Docker Compose passes `.env` through). See [`.env.example`](.env.example) for the full annotated list.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | **yes** | – | GitHub PAT used for ingestion |
| `DATABASE_URL` | yes (non-Docker) | – | SQLAlchemy URL, e.g. `postgresql+psycopg2://git_user:changeme@localhost:5432/git_analysis`. Docker Compose sets this automatically for the app container |
| `POSTGRES_PASSWORD` | no | `changeme` | Password of the Compose PostgreSQL container |
| `APP_PORT` / `POSTGRES_PORT` | no | `8000` / `5432` | Host ports published by Docker Compose |
| `ONLINE_AI_ENABLED` | no | `false` | Enable the online LLM (tier 1) |
| `ONLINE_AI_API_KEY` | no | – | API key for OpenAI or Google Gemini |
| `ONLINE_AI_MODEL` | no | `gemini-1.5-pro` | Model name. `gemini-*` uses Google's OpenAI-compatible endpoint, anything else uses OpenAI |
| `OLLAMA_BASE_URL` | no | `http://localhost:11434` (local) / `http://host.docker.internal:11434` (Compose) | Ollama server (tier 2) |
| `OLLAMA_MODEL` | no | `qwen2.5-coder:14b` | Ollama model name |
| `DEVELOPER_ANALYTICS_AI_URL` / `_TOKEN` / `_MODEL` | no | – | Optional OpenAI-compatible chat-completions endpoint for the developer *overview narrative*. If unset, a rule-based narrative is used |

### 3.2 Metric thresholds

These tune scoring and the red/green indicators on the dashboard. All are optional.

| Variable | Default | Meaning |
|---|---|---|
| `LARGE_PR_THRESHOLD_LINES` | `600` | Lines changed (additions + deletions) at which a PR counts as large |
| `FOLLOWUP_GOOD_THRESHOLD_HOURS` | `12` | Hours within which a follow-up commit after review feedback counts as prompt |
| `REQUESTED_CHANGES_RISKY_PCT` | `30` | % of PRs with requested changes above which a developer is flagged |
| `HIGH_COMMENTS_PER_PR_THRESHOLD` | `2` | Avg review comments per PR above which the trend indicator turns negative |
| `LEAD_TIME_HEALTHY_HOURS` | `48` | Max avg PR lead time (h) shown as healthy |
| `FIRST_REVIEW_HEALTHY_HOURS` | `24` | Max avg time to first review (h) shown as healthy |
| `REVIEW_COVERAGE_GOOD_PCT` | `80` | Min % of PRs reviewed shown as good |
| `APPROVAL_RATE_GOOD_PCT` | `60` | Min % of reviewed PRs approved shown as good |
| `CHANGE_FAILURE_ACCEPTABLE_PCT` | `35` | Max % of merged PRs with requested changes shown as acceptable |
| `CONFIDENCE_MIN_PRS` / `CONFIDENCE_MIN_COMMENTS` | `5` / `8` | PRs / review comments needed for *medium* analytics confidence |
| `CONFIDENCE_HIGH_PRS` / `CONFIDENCE_HIGH_COMMENTS` | `8` / `15` | PRs / review comments needed for *high* analytics confidence |

### 3.3 `config.yaml`

**`repositories`** lists the repositories to ingest. Only entries with `is_active: true` are fetched. Restart the backend after changing repositories (`docker compose restart app` in Docker).

**`teams`** (optional) turns on team-based analysis:

```yaml
teams:
  GrowthTeam:
    - name: "CF-Vaishnavi"
    - name: "CF-SinchanaBhat"
    - name: "CF-SrinidhiCV"
    - name: "CF-SantoshHM"
  IppPayFac:
    - name: "CF-NewMember"
```

- Each `name` is a **GitHub login**. Matching is case-insensitive. A login listed in two teams stays in the first one, and a warning is logged.
- **Only listed members are synced.** PRs opened by anyone else are skipped before their details are downloaded. On members' PRs, reviews, comments and commits written by non-members are not stored either. Commits whose author email isn't linked to a GitHub account count as the PR author's.
- PR-level review signals on a member's PR are kept as GitHub reports them, even when the reviewer isn't a member: review/approval/requested-changes counts, and first/last feedback time. A member's PR approved by someone outside the teams still counts as reviewed.
- **Skipped people are logged** at the end of every sync, so you can spot anyone missing from the list:
  ```
  WARNING - Skipped data from 2 GitHub user(s) not listed under 'teams' in config.yaml:
  WARNING -   not in any team: dependabot (3 pull_request(s)) in ichequenetwork/acquiring
  WARNING -   not in any team: john-doe (1 pull_request(s), 4 review(s), 2 comment(s)) in ichequenetwork/acquiring, ichequenetwork/token-management
  ```
- **Dashboard:** a **Team** selector appears above the cards. *All teams* combines every configured team, and choosing a team scopes the Team Overview (cards, DORA metrics, charts) and the Individual developer list to that team.
- Team changes apply when the backend starts and on every sync. Developers removed from all teams are hidden; their earlier data stays in the database. Run `scripts/clear_data.sql` and re-sync if you want it gone.
- Without a `teams` section, everyone is synced and shown together, as before.

---

## 4) Getting data into the dashboard

### 4.1 Sync from GitHub (UI)

Click **⟳ Sync from GitHub**. Ingestion imports every PR (open, closed or merged) that was **updated within the Lookback window**, for all active repositories, limited to team members if `teams` is configured. That includes its reviews, review comments, PR comments and commits. Re-running is safe: existing rows are updated, never duplicated. Only one sync runs at a time; a second request while one is running gets `409 Conflict`.

### 4.2 Sync from the command line

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"lookback_days": 30}'

# Poll until it reports "idle"
curl http://localhost:8000/api/ingest/status
```

Ingestion progress and errors (for example an invalid token) are printed in the backend logs (`docker compose logs -f app`).

### 4.3 Load sample (demo) data

`scripts/seed_data.sql` inserts 3 developers, 1 repository, 10 PRs, 30 commits and 45 review comments. Timestamps are moved so the data falls in the last few weeks and shows up on every chart. The backend must have started at least once, so the tables exist.

```bash
# Docker Compose
docker compose exec db psql -U git_user -d git_analysis -f /scripts/seed_data.sql

# Your own PostgreSQL
psql "postgresql://git_user:changeme@localhost:5432/git_analysis" -f scripts/seed_data.sql
```

If `teams` is configured, the sample developers (`alice_dev`, `bob_dev`, `carol_dev`) only appear when listed in a team, e.g. `Backend: [{name: alice_dev}, {name: bob_dev}]`.

To wipe **all** data (sample and synced) and start clean:

```bash
docker compose exec db psql -U git_user -d git_analysis -f /scripts/clear_data.sql
```

Then refresh the dashboard (**↺ Refresh**).

---

## 5) Running the API and UI separately (without Docker)

Use this for development, or if you prefer not to run the app in Docker. You need:

- **Python 3.11+**
- **Node.js 20.19+ or 22.12+** with npm
- **PostgreSQL 14+** (local install, or just the Compose database: see 5.1)

All commands below run from the project root unless noted.

### 5.1 Start PostgreSQL

**Option A: the Compose database only** (easiest)

```bash
docker compose up -d db
```

The default `DATABASE_URL` in `.env.example` already points at it: `postgresql+psycopg2://git_user:changeme@localhost:5432/git_analysis`.

**Option B: your own PostgreSQL**

```bash
psql -U postgres -c "CREATE USER git_user WITH PASSWORD 'changeme';"
psql -U postgres -c "CREATE DATABASE git_analysis OWNER git_user;"
```

Then set `DATABASE_URL` in `.env` to match.

### 5.2 Configure

```bash
cp .env.example .env      # set GITHUB_TOKEN (and DATABASE_URL if you used option B)
# edit config.yaml to list your repositories
```

### 5.3 Start the API (backend)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now at <http://localhost:8000> (Swagger docs at <http://localhost:8000/docs>). Tables are created on startup.

### 5.4 Start the UI (frontend), in a second terminal

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The Vite dev server forwards `/api/*` to `http://localhost:8000`. If your backend runs elsewhere, set `VITE_API_PROXY_TARGET`, e.g. `VITE_API_PROXY_TARGET=http://localhost:9000 npm run dev`.

### 5.5 Optional: serve the built UI from the backend (single port)

```bash
cd frontend && npm run build && cd ..
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

When `frontend/dist` exists, FastAPI serves the dashboard at <http://localhost:8000>, which is exactly what the Docker image does.

---

## 6) AI analysis (optional)

The developer **Summary** and **AI Metrics** tabs are enriched by an analyzer with three tiers. Each tier falls back to the next on failure:

| Tier | Used when | Handler |
|---|---|---|
| 1. Online AI | `ONLINE_AI_ENABLED=true` and `ONLINE_AI_API_KEY` set | OpenAI or Gemini (OpenAI-compatible chat API) |
| 2. Local Ollama | Ollama answers at `OLLAMA_BASE_URL` | Local model |
| 3. Rule-based | Always | Deterministic heuristics |

The LLM receives the developer's aggregate metrics plus a sample of up to 30 review comments (truncated) that reviewers left on their PRs. **With tier 1 enabled, that text is sent to the external provider**, so only enable it if that's acceptable for your repositories.

### Online AI (tier 1)

```env
ONLINE_AI_ENABLED=true
ONLINE_AI_API_KEY=sk-...         # OpenAI key, or a Google AI Studio key for Gemini
ONLINE_AI_MODEL=gpt-4o           # or gemini-1.5-pro
```

### Ollama running on your machine (tier 2)

```bash
# Install: https://ollama.com/download  (macOS/Linux: curl -fsSL https://ollama.com/install.sh | sh)
ollama pull qwen2.5-coder:7b     # ~4 GB; qwen2.5-coder:14b (~8 GB) is the default model
ollama serve                     # usually already running after install
curl http://localhost:11434/api/tags   # check it is reachable
```

Set `OLLAMA_MODEL=qwen2.5-coder:7b` in `.env` if you pulled a different model than the default.

- **Backend run directly**: works with the default `OLLAMA_BASE_URL` (`http://localhost:11434`).
- **Backend in Docker Compose**: the container reaches your machine at `http://host.docker.internal:11434` (the Compose default). On Linux, Ollama must listen on all interfaces: start it with `OLLAMA_HOST=0.0.0.0 ollama serve`.

### Ollama inside Docker Compose

```bash
# in .env:  OLLAMA_BASE_URL=http://ollama:11434   and   OLLAMA_MODEL=qwen2.5-coder:7b
docker compose --profile ollama up --build -d
docker compose exec ollama ollama pull qwen2.5-coder:7b
```

Running a model on CPU is slow, and a request taking more than 60 s falls back to the rule-based analyzer. The backend log says which tier was used, e.g. `Developer metrics analyzed via LocalOllamaHandler for alice` or `Ollama is not reachable at ...`. (The **Provider** badge on the developer card refers to the overview narrative, which stays `heuristic-fallback` unless `DEVELOPER_ANALYTICS_AI_URL` is configured.)

---

## 7) API reference

Full interactive documentation: **<http://localhost:8000/docs>**.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/active-repositories` | Active repositories from `config.yaml` |
| GET | `/api/teams` | Teams and members configured in `config.yaml` |
| GET | `/api/config/thresholds` | Active metric thresholds |
| GET | `/api/team-performance` | Team-level PR metrics (PRs created in the last 30 days) |
| GET | `/api/dora/team` | Team DORA-inspired metrics (30 days) + 8-week trends |
| GET | `/api/developers` | All known developers |
| GET | `/api/developers/{username}` | Aggregate metrics for one developer |
| GET | `/api/developers/{username}/analytics` | Review analytics + AI/heuristic summary for one developer |
| GET | `/api/developers/{username}/dora` | DORA-inspired metrics for one developer |
| GET | `/api/chart/pr-cycle-by-developer` | Avg cycle time per developer (30 days) |
| GET | `/api/chart/prs-per-week` | Merged PRs per ISO week (6 weeks); optional `?developer=<username>` |

The team-level endpoints (`/api/team-performance`, `/api/dora/team`, `/api/developers`, `/api/chart/*`) accept an optional `?team=<TeamName>` to scope results to one configured team. An unknown team returns `404`. Without it, they cover all configured teams, or everyone when no teams are configured.
| POST | `/api/ingest` | Start a GitHub sync in the background. Body: `{"lookback_days": 7}`. Returns `202`, or `409` if one is already running |
| GET | `/api/ingest/status` | `running` or `idle` |

> **Security note:** the API has **no authentication**. Anyone who can reach the port can read the metrics and trigger syncs (which use your GitHub token's rate limit). Run it on a trusted network or behind an authenticating reverse proxy.

---

## 8) What the metrics mean

This project only sees GitHub data (PRs, reviews, comments, commits), not deployments or incidents. So the delivery metrics are **GitHub-based proxies inspired by DORA**, not real DORA metrics.

| Metric | How it is calculated |
|---|---|
| **Merge frequency** | Merged PRs per week in the window (proxy for deployment frequency) |
| **Lead time (avg / median)** | PR created → merged |
| **Time to first review** | PR created → first review feedback from someone other than the author |
| **Review coverage** | % of PRs with at least one review or review comment |
| **Approval rate** | % of reviewed PRs with at least one approval |
| **Change-failure proxy** | % of merged PRs that received a "changes requested" review |
| **Recovery time** | For PRs with requested changes: last review feedback → merge |
| **Rework after feedback** | Commits pushed after the first review feedback |
| **First follow-up** | First review feedback → the author's next commit |
| **Comment themes** | Review comments sorted into style / correctness / testing / maintainability / performance / security by keyword |
| **Languages** | Taken from the file extensions of commented files |

Team metrics use PRs **created** in the last 30 days. Developer analytics use all stored PRs for that developer.

---

## 9) Project layout

```
app/
  main.py               FastAPI app: routes, background ingestion, SPA serving
  config.py             Settings (environment / .env)
  config_loader.py      Reads config.yaml (repositories)
  teams.py              Reads config.yaml teams, member filter, skipped-author log
  database.py           Engine/session, automatic table + column + index creation
  models.py             SQLAlchemy models: Developer, Repository, PullRequest, Commit, PullRequestComment
  github_ingestion.py   GitHub GraphQL client (pagination, batching, rate limits)
  ingest.py             Upserts fetched data into PostgreSQL
  queries.py            Team / developer / chart queries
  dora_metrics.py       DORA-inspired metrics and weekly trends
  developer_analytics.py  Per-developer review analytics + narrative
  ai_analyzers.py       Online LLM → Ollama → rule-based analyzer
frontend/               React + Vite dashboard (src/components/GitAnalyticsDashboard.tsx)
scripts/                seed_data.sql (demo data), clear_data.sql (wipe data)
tests/                  Backend unit tests
config.yaml             Repositories to analyse
Dockerfile              Multi-stage build: UI (Node) + API (Python) in one image
docker-compose.yml      PostgreSQL + app (+ optional Ollama)
```

---

## 10) Running the tests

The backend tests use an in-memory SQLite database and need no PostgreSQL, GitHub token or LLM:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Frontend type-check, lint and production build:

```bash
cd frontend
npm run lint
npm run build
```

---

## 11) Troubleshooting

| Symptom | Fix |
|---|---|
| A developer is missing after a sync | They aren't listed under `teams` in `config.yaml` (check the "not in any team" lines in the log), or their login is misspelled |
| Dashboard is empty | Click **⟳ Sync from GitHub**, or [load the sample data](#43-load-sample-demo-data). Check `config.yaml` lists the right repositories |
| Sync finishes but nothing appears | Check `docker compose logs app`. *"GitHub rejected the request (401/403)"* means the token is invalid or lacks access to the repository. Also try a bigger Lookback: only PRs updated in that window are imported |
| App container exits with a `github_token` / `database_url` "Field required" error | `GITHUB_TOKEN` is missing from `.env` (or `DATABASE_URL` when running without Docker) |
| `password authentication failed` after changing `POSTGRES_PASSWORD` | The password is fixed when the volume is first created. Run `docker compose down -v` (deletes data), then start again |
| `port is already allocated` | Set `APP_PORT` or `POSTGRES_PORT` in `.env` |
| `Could not create unique index ...` warning on startup | An older version stored duplicate PRs. Run `scripts/clear_data.sql` and sync again |
| Logs say `Ollama is not reachable` | See [AI analysis](#6-ai-analysis-optional). From Docker, Ollama on the host must be reachable at `host.docker.internal:11434` (on Linux start it with `OLLAMA_HOST=0.0.0.0`) |
| UI on port 5173 shows "Failed to load data" | The backend isn't running on port 8000, or set `VITE_API_PROXY_TARGET` |
