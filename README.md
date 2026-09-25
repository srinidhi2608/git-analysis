# Git Analysis

This project provides:
- A FastAPI backend for Git analytics endpoints — fetches PR data from GitHub and stores it in PostgreSQL
- A React dashboard at `frontend/src/components/GitAnalyticsDashboard.tsx` that reads **live data** from the backend

## 1) Prerequisites

- Python 3.11+  
- Node.js 18+ and npm  
- PostgreSQL running locally or remotely  
- GitHub Personal Access Token (PAT) with `repo` read access

## 2) Required properties to set **before start**

Create `.env` in the project root directory (see `.env.example` for all options):

```env
DATABASE_URL=postgresql://<db_user>:<db_password>@localhost:5432/git_analysis
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# ── AI / LLM configuration ──────────────────────────────────────────────────
# Tier 1 – Online AI (OpenAI-compatible, e.g. gpt-4o or gemini-1.5-pro)
ONLINE_AI_ENABLED=false
ONLINE_AI_API_KEY=
ONLINE_AI_MODEL=gpt-4o

# Tier 2 – Local Ollama (see section 2.1 below)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:14b

# ── Metric thresholds (all have sensible defaults) ───────────────────────────
# Uncomment and adjust as needed:
# LARGE_PR_THRESHOLD_LINES=600
# FOLLOWUP_GOOD_THRESHOLD_HOURS=12
# REQUESTED_CHANGES_RISKY_PCT=30
# HIGH_COMMENTS_PER_PR_THRESHOLD=2
# LEAD_TIME_HEALTHY_HOURS=48
# FIRST_REVIEW_HEALTHY_HOURS=24
# REVIEW_COVERAGE_GOOD_PCT=80
# APPROVAL_RATE_GOOD_PCT=60
# CHANGE_FAILURE_ACCEPTABLE_PCT=35
# CONFIDENCE_MIN_PRS=5
# CONFIDENCE_MIN_COMMENTS=8
```

Update `config.yaml` in the project root with repositories:

```yaml
repositories:
  - name: "owner/repo-name"
    is_active: true
```

Required property values:
- `DATABASE_URL`: valid PostgreSQL SQLAlchemy connection string
- `GITHUB_TOKEN`: PAT with access to read repository PR data
- `repositories[].name`: must be in `owner/repo` format
- `repositories[].is_active`: `true` to include repository in active list

### Metric threshold reference

| Variable | Default | Meaning |
|---|---|---|
| `LARGE_PR_THRESHOLD_LINES` | `600` | Lines-changed above which a PR is flagged as large |
| `FOLLOWUP_GOOD_THRESHOLD_HOURS` | `12` | Hours within which a review follow-up comment is considered prompt |
| `REQUESTED_CHANGES_RISKY_PCT` | `30` | % of PRs with requested changes above which a developer is flagged |
| `HIGH_COMMENTS_PER_PR_THRESHOLD` | `2` | Avg review comments per PR above which trend indicator is positive |
| `LEAD_TIME_HEALTHY_HOURS` | `48` | Max avg PR lead time (h) for a "healthy" DORA score |
| `FIRST_REVIEW_HEALTHY_HOURS` | `24` | Max avg time-to-first-review (h) for a "healthy" indicator |
| `REVIEW_COVERAGE_GOOD_PCT` | `80` | Min % of PRs reviewed for a "good" coverage indicator |
| `APPROVAL_RATE_GOOD_PCT` | `60` | Min % of PRs approved for a "good" approval rate indicator |
| `CHANGE_FAILURE_ACCEPTABLE_PCT` | `35` | Max % of PRs with requested changes for acceptable change-failure proxy |
| `CONFIDENCE_MIN_PRS` | `5` | Minimum PRs required for medium confidence in analytics |
| `CONFIDENCE_MIN_COMMENTS` | `8` | Minimum review comments required for medium confidence |

### 2.1 Configuring local Ollama (Tier 2 AI)

Ollama lets you run open-source LLMs on your own machine. When `ONLINE_AI_ENABLED=false` (the default) and Ollama is running, the app automatically uses it for developer metric analysis — no API key required.

**Install Ollama**

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows — download the installer from https://ollama.com/download
```

**Pull a model** (one-time download, ~8 GB for the default):

```bash
ollama pull qwen2.5-coder:14b
```

You can use a smaller model if disk space or RAM is limited:

```bash
ollama pull qwen2.5-coder:7b   # ~4 GB
ollama pull codellama:7b        # ~4 GB
```

Then set the matching model name in `.env`:

```env
OLLAMA_MODEL=qwen2.5-coder:7b
```

**Start the Ollama server** (runs automatically after install on most systems):

```bash
ollama serve   # or: systemctl start ollama
```

Verify it is reachable:

```bash
curl http://localhost:11434/api/tags
```

The backend probes this endpoint at startup and automatically falls back to the heuristic engine if Ollama is unreachable.

**3-tier fallback summary**

| Tier | Condition | Handler |
|---|---|---|
| 1 – Online AI | `ONLINE_AI_ENABLED=true` and `ONLINE_AI_API_KEY` is set | Calls OpenAI / Gemini API |
| 2 – Local Ollama | Ollama `/api/tags` is reachable | Calls local model |
| 3 – Heuristic | Always available | Rule-based scoring |

## 3) Backend setup and start commands

From the project root directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend starts at `http://localhost:8000`.  
Database tables are created automatically on first start.

### 3.1 PostgreSQL note for existing databases

No manual DB step is required for a fresh setup. On startup, the app creates missing tables and adds missing columns automatically.

If you are upgrading an existing PostgreSQL database, first pull the latest code and restart the backend. The schema bootstrap now uses PostgreSQL-compatible timestamp types.

If startup previously failed on an older build with an error like `type "datetime" does not exist`, you normally do **not** need to change data manually; just restart with the updated code.

If you prefer to patch an existing database manually before restarting, run:

```sql
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS first_review_comment_at TIMESTAMP NULL;
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS last_review_comment_at TIMESTAMP NULL;
ALTER TABLE commits ADD COLUMN IF NOT EXISTS committed_at TIMESTAMP NULL;
```

You can also verify the added columns with:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('pull_requests', 'commits')
ORDER BY table_name, ordinal_position;
```

## 4) Backend endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/docs` | Swagger UI |
| GET | `/active-repositories` | Lists active repos from `config.yaml` |
| GET | `/api/team-performance` | Team-level PR metrics (last 30 days) |
| GET | `/api/dora/team` | Team-level DORA-inspired GitHub delivery metrics and weekly trends |
| GET | `/api/developers` | List all known developers |
| GET | `/api/developers/{username}` | Metrics for a specific developer |
| GET | `/api/developers/{username}/analytics` | Saved PR review analytics + AI/heuristic summary for a developer |
| GET | `/api/developers/{username}/dora` | Individual DORA-inspired GitHub delivery metrics and weekly trends |
| GET | `/api/config/thresholds` | Returns the active metric threshold values (sourced from `.env`) |
| GET | `/api/chart/pr-cycle-by-developer` | Avg cycle time per developer (for bar chart) |
| GET | `/api/chart/prs-per-week` | PRs merged per ISO week (for line chart); accepts optional `?developer=<username>` |
| POST | `/api/ingest` | Triggers GitHub data fetch and saves PRs to Postgres (runs in background) |

## 5) Triggering data ingestion

Data is fetched from the GitHub GraphQL API using the PAT in `.env`.  
You can trigger ingestion two ways:

**From the UI** — click the **⟳ Sync from GitHub** button in the dashboard header. The dashboard includes a **Lookback** field that sends the requested number of days to the backend.

**From the command line / curl:**
```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"lookback_days": 30}'
```

Ingestion fetches closed/merged PRs for the selected lookback window (default: 7 days) for all active repositories and upserts them into Postgres.
Saved pull request records now also capture:
- PR/review comment text and timestamps
- Review decisions / requested-changes signals
- Changed-file and diff-size metadata
- Commit timestamps used for review follow-up estimates

## 6) Dashboard UI setup

### 6.1 Install frontend dependencies

From the project root:

```bash
cd frontend
npm install
```

### 6.2 Start the React app

```bash
npm run dev
```

Open `http://localhost:5173` (or the URL shown in terminal).

> The Vite dev server proxies `/api/*` requests to the backend at `http://localhost:8000`, so no CORS configuration is needed during development.

### 6.3 What to verify on screen

1. Page loads with title: **Engineering Performance Dashboard**
2. Header shows two buttons: **⟳ Sync from GitHub** and **↺ Refresh**
3. Two tabs are visible: **Team Overview** and **Individual Performance**
4. On first load with an empty database, charts show an empty-state message prompting you to sync
5. Click **⟳ Sync from GitHub** — a green status message confirms ingestion has started
6. After a few seconds, click **↺ Refresh** — charts and KPI cards populate with real GitHub data
7. **Team Overview** tab:
   - 3 KPI cards: Active Developers, Avg PR Cycle Time, PRs Merged (30d)
   - Bar chart: **Average PR Cycle Time by Developer**
   - Line chart: **Total PRs Merged per Week**
8. **Individual Performance** tab:
   - Developer dropdown to select a specific GitHub user
   - KPI cards update for the selected developer
   - Charts filter to show that developer's data only
   - **Four inner tabs** organise the detail views:
     - **Summary** — AI-generated or heuristic overall highlights, strengths, and improvement areas; detected programming languages shown as tags
     - **DORA Metrics** — individual DORA-inspired GitHub delivery section: merge frequency, lead time, review coverage, approval rate, and change-failure proxy trend
     - **AI Metrics** — grounded summary, coding standards score, DRY/WET observations, reviewer rigor score, and comment-theme breakdown
     - **Reviewers** — list of reviewers ranked by number of review comments left on the developer's PRs
9. Hover over chart points/bars — tooltips appear

## 7) DORA-inspired GitHub metrics added to the dashboard

The app now includes a GitHub-native interpretation of DORA software delivery ideas. Because this project currently analyzes pull requests, reviews, comments, and commits — not production deploys or incident systems — the dashboard uses **proxies** that fit GitHub data.

### Team-level and individual-level metrics

- **Merge Frequency**  
  Proxy for deployment frequency. Calculated as merged PRs per week over the dashboard window.

- **Average Lead Time**  
  Proxy for lead time for changes. Calculated from PR creation to merge time.

- **Median Lead Time (P50)**  
  Median PR lead time, used to reduce outlier distortion when a few PRs stay open much longer than the rest.

- **Time to First Review**  
  Measures how long a PR waits before the first saved review feedback appears. Useful for identifying review-queue bottlenecks.

- **Review Coverage Rate**  
  Percentage of PRs that received recorded review activity or review comments.

- **Approval Rate**  
  Percentage of reviewed PRs that received at least one approval.

- **Change Failure Proxy Rate**  
  GitHub-specific quality proxy for DORA change failure rate. Calculated as the percentage of merged PRs in the window that received requested changes during review.

- **Average Recovery Time After Review Feedback**  
  GitHub-specific recovery proxy inspired by MTTR. Calculated as the average time from the last saved review feedback to merge for PRs that had requested changes.

### Important note

These are **DORA-inspired engineering workflow metrics**, not exact production DORA metrics. True deployment frequency, change failure rate, and mean time to restore service require deployment and incident data that is outside the current GitHub-only dataset.

## 8) Sample seed data

The `scripts/` directory contains SQL files for local development and demos.

### Load sample data

Seeds 3 developers, 1 repository, 10 pull requests, 30 commits, and 45 review comments (multiple comments per PR covering realistic code-review conversations):

```bash
psql $DATABASE_URL -f scripts/seed_data.sql
```

The script is idempotent — it uses `ON CONFLICT DO NOTHING / DO UPDATE` guards, so running it twice is safe. To reset to a completely clean state first:

```bash
psql $DATABASE_URL -f scripts/clear_data.sql
psql $DATABASE_URL -f scripts/seed_data.sql
```

### Clear all data

```bash
psql $DATABASE_URL -f scripts/clear_data.sql
```

This truncates all application tables and resets identity sequences. It is also safe to run multiple times.
