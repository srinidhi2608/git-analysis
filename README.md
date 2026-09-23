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

Create `.env` in the project root directory:

```env
DATABASE_URL=postgresql://<db_user>:<db_password>@localhost:5432/git_analysis
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
DEVELOPER_ANALYTICS_AI_URL=
DEVELOPER_ANALYTICS_AI_TOKEN=
DEVELOPER_ANALYTICS_AI_MODEL=
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
- `DEVELOPER_ANALYTICS_AI_URL`, `DEVELOPER_ANALYTICS_AI_TOKEN`, `DEVELOPER_ANALYTICS_AI_MODEL`: optional AI provider settings for natural-language developer review summaries. If omitted, the app falls back to an in-app heuristic summary.
- `repositories[].name`: must be in `owner/repo` format
- `repositories[].is_active`: `true` to include repository in active list

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
   - DORA-inspired GitHub delivery section shows merge frequency, lead time, review coverage, approval rate, and a change-failure proxy trend for the selected developer
   - AI Developer Review Analytics section shows grounded summary, review metrics, comment-theme breakdown, and PR churn insights for the selected developer
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
  GitHub-specific quality proxy for DORA change failure rate. Calculated as the percentage of reviewed PRs that received requested changes.

- **Average Recovery Time After Review Feedback**  
  GitHub-specific recovery proxy inspired by MTTR. Calculated as the average time from the last saved review feedback to merge for PRs that had requested changes.

### Important note

These are **DORA-inspired engineering workflow metrics**, not exact production DORA metrics. True deployment frequency, change failure rate, and mean time to restore service require deployment and incident data that is outside the current GitHub-only dataset.
