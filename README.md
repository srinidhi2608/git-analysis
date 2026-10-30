# Git Analysis

**Git Analysis** is a self-hosted engineering-metrics dashboard built from GitHub pull-request data.

It pulls PRs, reviews, review comments and commits for the repositories you choose from the GitHub GraphQL API, stores them in PostgreSQL, and shows them in a web dashboard:

- **Team overview**: for all teams or one selected team, PR cycle time, merged PRs per week, and **DORA-inspired delivery metrics** (merge frequency, lead time, time to first review, review coverage, approval rate, change-failure proxy, recovery time) with weekly trends.
- **Individual performance**: for each developer, PR size, review-comment density, requested-changes rate, rework after feedback, follow-up speed, recurring review themes, top reviewers and the languages they work in.
- **DevOps Integration (optional)**: connects **Azure Boards** (user stories, bugs, tasks) and **Azure Pipelines** (deployments) to the GitHub data. You can filter stories by tag, date range, team or person, export reports, see each developer's and tester's delivery, and get **DORA metrics** (deployment frequency, lead time for changes, change failure rate, failed deployment recovery time) for the team or a single person. The definitions follow [Apache DevLake](https://devlake.apache.org/).
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
7. [DevOps Integration (Azure DevOps)](#7-devops-integration-azure-devops)
8. [Production deployment (HTTPS, certificates, hardening)](#8-production-deployment-https-certificates-hardening)
9. [API reference](#9-api-reference)
10. [What the metrics mean](#10-what-the-metrics-mean)
11. [Project layout](#11-project-layout)
12. [Running the tests](#12-running-the-tests)
13. [Troubleshooting](#13-troubleshooting)

---

## 1) How it works

```
config.yaml ──► GitHub GraphQL API ──► ingestion (POST /api/ingest) ─────────┐
(repos, teams)                                                              ▼
              Azure DevOps REST API ──► DevOps sync (POST /api/devops/sync) ─► PostgreSQL
              (Boards work items, Pipelines runs)                           │
                                                                            ▼
 Browser ◄── React dashboard ◄── FastAPI (/api/*) ◄── metrics, DORA, developer analytics, story reports
                                                        └─► AI summary: Online LLM → Ollama → rule-based
```

| Component | Technology | Where |
|---|---|---|
| API / backend | Python 3.11+, FastAPI, SQLAlchemy | `app/` |
| Dashboard UI | React 19, TypeScript, Vite, Tailwind, Recharts | `frontend/` |
| Database | PostgreSQL 16 | Docker volume `postgres_data` |
| AI (optional) | OpenAI / Gemini API, or a local Ollama model | `app/ai_analyzers.py` |
| Azure DevOps (optional) | Azure DevOps REST API 7.1 (Boards + Pipelines) | `app/azure_devops.py`, `app/devops_*.py` |

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

The dashboard starts empty. Click **⟳ Sync from GitHub** in the header. The button stays in "Syncing…" until ingestion finishes, then the dashboard reloads and shows how many PRs were updated and how many GitHub API requests were used. The **date range** picker next to it sets both which PR activity Sync imports (for example **Last 90 days** for a first import) and the period the dashboard shows. See [4.6](#46-choose-a-date-and-time-range).

After each sync the data is also saved as a [snapshot](#44-snapshots-keep-data-without-re-fetching) in `./snapshots`, so it comes back automatically even after `docker compose down -v` or a rebuild.

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
| `AZURE_DEVOPS_PAT` | no | – | Azure DevOps personal access token (Work Items: Read, Build: Read) for the DevOps Integration page |
| `SNAPSHOT_DIR` | no | `snapshots` | Where snapshots are written (`./snapshots` on the host with Docker Compose) |
| `SNAPSHOT_AUTOLOAD` | no | `true` | Load the newest snapshot at startup when the database has no pull requests |
| `SNAPSHOT_AFTER_SYNC` | no | `true` | Save a snapshot automatically after every successful sync |

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
    - name: "Vaishnavi K"                       # display name shown on every page
      github: "CF-Vaishnavi"                    # GitHub login
      azure_devops: "vaishnavi.k@company.com"   # Azure DevOps identity (optional, see section 7)
      role: developer                           # optional: developer | tester
    - name: "Sinchana Bhat"
      github: "CF-SinchanaBhat"
      azure_devops: ["sinchana.b@company.com", "Sinchana B"]   # several identities are fine
      role: tester
    - name: "Santosh HM"
      github: "CF-SantoshHM"
  IppPayFac:
    - name: "CF-NewMember"                      # short form: name is the GitHub login
```

Each member is **one person**, who often has different names in GitHub and Azure DevOps:

| Key | Meaning |
|---|---|
| `name` | Display name used on every page: dropdowns, charts, tables and reports |
| `github` | GitHub login. Leave it out for someone with no GitHub account (e.g. a QA engineer who only works in Azure Boards) |
| `azure_devops` | The person's Azure DevOps identity as shown in *Assigned To*: email / unique name (e.g. `DOMAIN\user` on Azure DevOps Server), or display name. One value or a list |
| `role` | `developer` or `tester`, shown on the DevOps Integration page |

- **Short form:** an entry with only `name` (or a plain string) treats `name` as the GitHub login. That's the older format, and it keeps working. The older `azure:` key is also accepted for `azure_devops`.
- **Matching** is case-insensitive. A person listed in two teams stays in the first one, and an Azure DevOps identity configured for two people stays with the first. Both cases log a warning.
- **Checking the links:** the **People & identities** panel on the DevOps Integration page (or `GET /api/people`) shows each person's GitHub login and Azure DevOps identities, how many PRs and work items matched them, and the Azure identities that match nobody.
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

Click **⟳ Sync from GitHub**. Ingestion imports every PR (open, closed or merged) that was **updated within the selected date range**, for all active repositories, limited to team members if `teams` is configured. That includes its reviews, review comments, PR comments and commits. Re-running is safe: existing rows are updated, never duplicated. Only one sync runs at a time; a second request while one is running gets `409 Conflict`.

**Re-syncs are incremental.** Each stored PR remembers GitHub's `updatedAt`. On the next sync, PRs whose `updatedAt` hasn't changed are not downloaded again; only new or changed PRs are fetched and upserted. The sync message and the log report the numbers, for example `Sync complete: 3 PR(s) updated, 41 unchanged (not downloaded again), 2 GitHub API request(s)`.

Tick **Full refresh on sync** (or send `"full_refresh": true`) to download every PR in the range again. You need this after adding a new member to `teams`: their reviews on PRs that are already stored are only picked up when those PRs are re-downloaded.

### 4.2 Sync from the command line

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"date_from": "2026-09-01T00:00:00Z", "date_to": "2026-10-01T00:00:00Z"}'
# add "full_refresh": true to re-download everything; omit date_to for "until now";
# {"lookback_days": 30} (the last 30 days) still works too

# Poll until it reports "idle"; "last_run" holds the statistics of the last sync
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

Then refresh the dashboard (**↺ Refresh**). An empty database is refilled from the newest snapshot on the next start; delete the files in `./snapshots/` too if you want it to stay empty.

### 4.4 Snapshots: keep data without re-fetching

The PostgreSQL volume already keeps data between `docker compose stop` / `up`. Snapshots also cover `docker compose down -v`, rebuilds, and moving the app to another machine:

- **Saved automatically after every successful sync**, or on demand with **Save snapshot** in the header (`POST /api/snapshots`). Files are gzipped JSON in `./snapshots/` (`snapshot-<UTC time>.json.gz`); the newest 5 are kept.
- **Loaded automatically at startup** when the database has no pull requests, e.g. after `docker compose down -v`. You can also restore with **Load latest snapshot** (`POST /api/snapshots/load`, optional body `{"name": "snapshot-....json.gz"}`). Loading upserts, so it never duplicates rows. After loading, team assignments are re-applied from `config.yaml`.
- After restoring, a normal **Sync** only downloads PRs that changed on GitHub since the snapshot.

To start completely fresh, delete the files in `./snapshots/` as well as running `docker compose down -v`.

---

### 4.5 Filter by target branch

PRs are often merged into several branches (`main`, `develop`, `release/*`, hotfix branches). The **Target branch** dropdown, above the cards on Team Overview and Individual Performance and in the DevOps Integration filters, limits every PR-based number to PRs that **target** the selected branches:

- Pick one or several branches; the list shows how many PRs target each.
- **All branches** clears the filter.
- The selection is remembered in your browser.

It applies to the cards, DORA metrics, charts, developer review analytics, and the lead time on the DevOps page. API: add `?branches=main&branches=develop` to the PR-based endpoints (section 9).

The target branch is recorded when PRs are synced. PRs synced before this feature have no target branch until you sync once with **Full refresh on sync**. Until then they are excluded whenever a branch is selected, and the dropdown shows how many PRs that affects.

### 4.6 Choose a date and time range

The **date range** picker in the header replaces the old "Lookback days" field. One range drives every page, and both sync buttons:

- **Presets:** Last 24 hours, Last 7 / 14 / 30 / 90 days, This month, Last month. Presets roll forward: "Last 30 days" always means the 30 days up to now.
- **Custom range:** pick a start and end **date and time**, then **Apply**. Times are in your browser's time zone and are sent to the API in UTC.
- **What it filters:** Team Overview and Individual Performance count PRs **created or merged** in the range (merge-based figures count PRs **merged** in it). The DevOps Integration page uses it for work items and pipeline runs.
- **What Sync fetches:** **⟳ Sync from GitHub** imports PRs **updated** in the range, and **⟳ Sync Azure DevOps** imports work items changed and pipeline runs finished in it. Ranges are end-exclusive.
- The range is remembered in your browser.

API: `date_from` / `date_to` take an ISO date (`2026-10-01`, a whole day) or a date-time (`2026-10-01T09:30:00Z`, or with an offset such as `+05:30`; no offset means UTC). See section 9.

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

## 7) DevOps Integration (Azure DevOps)

The **DevOps Integration** tab adds Azure DevOps to the GitHub data, for setups like this one:

- **Azure Boards** holds the user stories, bugs and tasks, assigned to developers and testers.
- **Azure Pipelines** builds and deploys the GitHub repositories' branches.
- **GitHub PRs** reference their story with `AB#<work item id>` in the title. This is the Azure Boards GitHub integration convention, e.g. `AB#11353 pre auth`.

It is modelled on [Apache DevLake](https://github.com/apache/devlake)'s Azure DevOps and DORA plugins. It uses the same deployment rules, the same lead time definition, and DevLake's DORA 2023 benchmarks. It all runs inside this application, so DevLake doesn't need to be installed.

### 7.1 What the page shows

| Section | What you get |
|---|---|
| **Filters** | Date range (with 7/30/90-day shortcuts), team, developer/tester, work item types, **tags** (any or all of the selected tags), and finished-only or finished + open items. Every section below follows the filters. |
| **DORA metrics** | Deployment frequency, lead time for changes, change failure rate and failed deployment recovery time, each with its **Elite / High / Medium / Low** level, plus a weekly chart of deployments and lead time. With a person or team selected, the metrics use **their** pipeline runs (see 7.4). |
| **Pipeline runs** | Every run behind the numbers, with result, repository, branch and *Requested for*. With a person or team selected, a *Why it is included* column shows how each run was attributed (requested for them, built or validated their PR, or shipped their PR). Tick **Production deployments only** to narrow it. Also in the Markdown report. |
| **Story summary** | Items finished, story points, throughput per week, median cycle time (active → closed), median lead time (created → closed), GitHub PRs linked via `AB#`. |
| **Efficiency by developer / tester** | Per person, in the date range: work items finished, story points, median cycle time, items per week, share of the team's finished items, **bugs reported** (useful for testers) and GitHub PRs merged. Click a row to see that person's DORA metrics. |
| **Work items** | The filtered stories with links to Azure Boards, tags, points, closed date, cycle time and linked PRs. |
| **Reports** | **⬇ CSV** (all filtered work items) and **⬇ Report (Markdown)**: filters, summary, DORA table, efficiency table and the work item list, ready to paste into a wiki or email. **Print** prints the page. |

### 7.2 Connect the application to Azure DevOps

**Step 1: Create a personal access token (PAT)**

1. In Azure DevOps, open **User settings** (top right) → **Personal access tokens** → **New Token**.
2. Choose the organization that holds your boards and pipelines.
3. Under **Scopes**, choose *Custom defined* and tick **Work Items → Read** and **Build → Read**.
4. Create the token and copy it.

**Step 2: Add the token to `.env`**

```env
AZURE_DEVOPS_PAT=your-token-here
```

**Step 3: Configure `config.yaml`**

```yaml
azure_devops:
  organization: "your-org"            # https://dev.azure.com/your-org (a full URL also works)
  projects: ["Payments"]              # Azure DevOps project names
  work_item_types: ["User Story", "Bug", "Task"]
  # Pipelines whose name matches this regex are deployments ...
  deployment_pattern: "(?i)deploy|release"
  # ... and these go to production (leave empty to count every deployment).
  production_pattern: "(?i)prod"
  # Optional: work items that are production incidents (see 7.4).
  incidents:
    work_item_types: ["Bug"]
    tags: ["Incident"]
```

**Step 4: Link people across GitHub and Azure DevOps**

GitHub logins and Azure DevOps names are usually different. List each person once under `teams`, with both identities and the name to show on the pages:

```yaml
teams:
  GrowthTeam:
    - name: "Vaishnavi K"                       # shown on the pages and in reports
      github: "CF-Vaishnavi"                    # GitHub login
      azure_devops: "vaishnavi.k@company.com"   # as shown in Azure Boards "Assigned To"
      role: developer
    - name: "Tom QA"                            # a tester with no GitHub account
      azure_devops: ["tom.q@company.com", "Tom Quinn"]
      role: tester
```

To find the exact Azure DevOps identities, open a work item and hover over *Assigned To*. Or sync once and open **People & identities** on the DevOps Integration page: it lists every Azure identity in the data that isn't linked yet, with a ready-to-edit `config.yaml` snippet. The sync status also names the identities it skipped.

Matching order for an Azure identity:

1. the person's `azure_devops` values
2. their `name` (if it equals their Azure display name)
3. their GitHub login, or an email whose part before `@` is the GitHub login

Across all pages, filters and reports show the `name`. The underlying data still uses the GitHub login, so a person's GitHub PRs and Azure work items are counted together: in their efficiency row, in "PRs merged", and in their person-level DORA metrics.

As with GitHub data, when `teams` is configured only work items **assigned to or created by** listed members are stored, and everyone else is logged as `not in any team: ...`. All pipeline runs are stored; a run's **Requested for** identity is matched to people the same way, so runs someone started count for them (section 7.4).

**Step 5: Restart and sync**

```bash
docker compose up -d --build     # or restart the backend
```

Open **DevOps Integration**, choose a date range in the header (**Last 90 days** is a good first sync) and click **⟳ Sync Azure DevOps**. The sync fetches:

- work items of the configured types **changed** in that range (WIQL with time precision + `workitemsbatch`, 200 at a time)
- **completed** pipeline runs finished in that range (Builds API `minTime`/`maxTime`, paged)

Re-syncing updates existing rows. The data is included in snapshots (section 4.4).

From the command line:

```bash
curl -X POST http://localhost:8000/api/devops/sync -H "Content-Type: application/json" \
  -d '{"date_from": "2026-08-01T00:00:00Z", "date_to": "2026-11-01T00:00:00Z"}'
curl http://localhost:8000/api/devops/sync/status
```

**Lead time needs the merge commit.** PRs synced from GitHub now store their merge commit SHA, so a deployment of exactly that commit is matched. PRs synced before this version get it on the next GitHub sync with **Full refresh on sync**. Until then they fall back to "the first production deployment of that repository after the merge".

**Self-hosted Azure DevOps Server** (2022.1 or later, which supports REST API 7.1): set `organization` to the full collection URL, e.g. `https://ado.company.com/tfs/DefaultCollection`.

### 7.3 Try it with demo data

```bash
# after the GitHub sample data (section 4.3):
docker compose exec db psql -U git_user -d git_analysis -f /scripts/seed_devops.sql
```

This adds 22 work items (stories, testing tasks, bugs and 2 incidents) and about 70 pipeline runs for the last 6 weeks. It also links the sample PRs to stories with `AB#`, adds runs that people started themselves from an Azure Repos mirror named `platform`, and fills the DevOps Integration page. Without a PAT the page shows "Not configured", but the stored demo data is still displayed. To see roles and the per-person view, use the `teams` example at the top of `scripts/seed_devops.sql`.

### 7.4 How the DORA metrics are calculated

The definitions follow Apache DevLake (`backend/plugins/dora`), and the levels use the **DORA 2023** benchmarks from DevLake.

| Metric | Calculation | Elite | High | Medium | Low |
|---|---|---|---|---|---|
| **Deployment frequency** | Successful production deployments. The level comes from the median number of *days with a deployment* per week (and per month) in the range | ≥ 5 days/week (on-demand) | ≥ 1 day/week | ≥ 1 day/month | fewer |
| **Lead time for changes** | Median of PR *first commit* → finish of the production deployment that shipped it | < 1 day | < 1 week | < 1 month | longer |
| **Change failure rate** | Deployments that caused an incident ÷ deployments; without incidents configured, failed deployment runs ÷ all deployment runs | ≤ 5% | ≤ 10% | ≤ 15% | > 15% |
| **Failed deployment recovery time** | Median of deployment finish → incident closed; without incidents, failed run → next successful run of the same pipeline | < 1 hour | < 1 day | < 1 week | longer |

The underlying rules:

- **Deployments:** completed runs of pipelines whose name matches `deployment_pattern`, and `production_pattern` if set. `succeeded` and `partiallySucceeded` count as success; `failed`, `canceled` and `none` count as failure. These are DevLake's Azure DevOps mappings.
- **Which deployment shipped a PR:** the successful production deployment whose commit equals the PR's merge commit. Otherwise it's the first successful production deployment of the same repository after the merge. Repositories match as `owner/repo` (how Azure Pipelines names GitHub repositories) or by the bare name (`repo`, how Azure Repos and mirrors name them).
- **Incidents:** work items matching `incidents` (type and/or tag). As in DevLake, each incident is attributed to the **last successful production deployment before it was created**.
- **Person / team scope:** a pipeline run belongs to a person (or to a team's members) when **any** of these is true:
  1. it was **requested for** them in Azure Pipelines (manual runs, their pushes), matched through `azure_devops`, display name or GitHub login in `teams`;
  2. it **built** the merge commit or one of the commits of their PR, or **validated** their PR (`refs/pull/<n>/merge`);
  3. it is the deployment that **shipped** their PR (rule above).

  Deployment frequency counts their successful production deployments. Change failure rate uses their production deployment attempts (plus each PR's first attempt after the merge), and lead time uses their PRs. Testers without a GitHub account are covered by rule 1.
- **Stories:** cycle time is *Activated → Closed* (Created → Closed if never activated); lead time is *Created → Closed*. "Finished in range" means the *Closed* date is in the range.

**Sources:** the [DevLake GitHub data source demo](https://devlake.apache.org/livedemo/DataSources/GitHub/), [DevLake source code](https://github.com/apache/devlake), and [Atlassian's overview of DORA metrics](https://www.atlassian.com/devops/frameworks/dora-metrics).

---

## 8) Production deployment (HTTPS, certificates, hardening)

The Docker image is ready for production as is. Certificates are handled by the container's entrypoint (`docker/entrypoint.sh`), which runs as root only to prepare them, then starts the app as the unprivileged `appuser`.

### 8.1 Certificates

Put your files in `certs/`. The Dockerfile copies this folder into the image, and `docker-compose.yml` also mounts it read-only at runtime, so a restart picks up renewed certificates:

```
certs/
├── tls/
│   ├── server.crt      HTTPS certificate: full chain (server certificate first, then intermediates)
│   ├── server.key      its private key, PEM (encrypted keys: set TLS_KEY_PASSWORD)
│   └── client-ca.crt   optional: require client certificates signed by this CA (mutual TLS)
└── ca/
    └── *.crt / *.pem   extra CAs to trust for outbound HTTPS
```

Everything under `certs/` except its README is git-ignored, so keys are never committed. Keys baked into an image can be read by anyone who can pull it. Only push such images to a private registry, or keep `certs/` empty at build time and rely on the runtime mount.

At startup, the entrypoint does the following:

| Step | What happens |
|---|---|
| Extra CAs (`certs/ca/*.crt`, `*.pem`) | Each file is validated, then appended to the system CA bundle in `/app/run/ca-bundle.pem`. That bundle is exported as `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`, so GitHub (Enterprise), Azure DevOps (Server), the LLM endpoints and a TLS-intercepting corporate proxy all work. |
| HTTPS (`certs/tls/server.crt` + `server.key`) | Turns on automatically (`TLS_ENABLED=auto`). The key is checked against the certificate (and `TLS_KEY_PASSWORD`). It's copied to `/app/run/tls`, readable only by `appuser`, so a host key mounted as root-only `0600` still works. Uvicorn then serves HTTPS on the same port. |
| Mutual TLS (`certs/tls/client-ca.crt`) | Clients must present a certificate signed by that CA. |
| Fail fast | An invalid CA file, a key that doesn't match the certificate, a wrong key password, or `REQUIRE_TLS=true` without a certificate stops the container with a clear `entrypoint: ERROR: ...` message. |

**Quick test with a self-signed certificate** (browsers will warn; use a real certificate in production):

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout certs/tls/server.key -out certs/tls/server.crt \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
docker compose up -d --build
curl -k https://localhost:8000/health
```

**Database over TLS** (managed PostgreSQL): put the server's CA in `certs/ca/` and use e.g.
`DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db?sslmode=verify-full&sslrootcert=/app/certs/ca/db-ca.crt`.

### 8.2 Run in production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

`docker-compose.prod.yml` (requires Docker Compose 2.24+):

- **requires HTTPS** (`REQUIRE_TLS=true`) and publishes it on `APP_PORT` (default **443**)
- **does not publish PostgreSQL** on the host
- turns off the API docs, and limits CORS to `CORS_ALLOW_ORIGINS`
- uses `restart: always` and a 1 GB memory limit

To use an external database, set `DATABASE_URL` in an override and remove the `db` service.

**Behind a reverse proxy or load balancer** that terminates TLS: leave `certs/tls` empty (the app speaks HTTP), use the base `docker-compose.yml` (don't set `REQUIRE_TLS`), and set `FORWARDED_ALLOW_IPS` to the proxy's IP, so client IPs and the `https` scheme (for HSTS) come from `X-Forwarded-*`.

### 8.3 What is hardened

- **Container:**
  - runs as the non-root `appuser` (uid 1000)
  - application code is root-owned and read-only; only `snapshots/` and `/app/run` are writable
  - compose sets `no-new-privileges`, drops all Linux capabilities except the few the entrypoint needs (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`), and rotates logs (10 MB × 5)
- **Server:** uvicorn with `--proxy-headers`, no `Server` header, graceful shutdown (`GRACEFUL_SHUTDOWN_TIMEOUT`, default 30 s) and a keep-alive timeout. The healthcheck (`docker/healthcheck.py`) probes HTTP or HTTPS as configured.
- **HTTP headers:** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and **HSTS** on HTTPS requests.
- **API surface:** `CORS_ALLOW_ORIGINS` (default `*`; the bundled UI is same-origin and needs none). `ENABLE_API_DOCS=false` removes `/docs`, `/redoc` and `/openapi.json`.
- **Secrets:** `GITHUB_TOKEN`, `AZURE_DEVOPS_PAT` and AI keys come from `.env` / the environment, never from the image. The TLS key password is passed to uvicorn through an environment variable, not the command line.

### 8.4 Production settings

| Variable | Default | Purpose |
|---|---|---|
| `TLS_ENABLED` | `auto` | `auto`: HTTPS when `certs/tls/server.crt` + `server.key` exist; `true` / `false` to force |
| `REQUIRE_TLS` | `false` | Refuse to start without a certificate |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | `/app/certs/tls/server.crt` / `.key` | Certificate and key paths inside the container |
| `TLS_KEY_PASSWORD` | – | Password of an encrypted private key |
| `TLS_CLIENT_CA_FILE` | `/app/certs/tls/client-ca.crt` | Enables mutual TLS when the file exists |
| `CA_CERTS_DIR` | `/app/certs/ca` | Extra CA certificates to trust |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated allowed origins (empty = none) |
| `ENABLE_API_DOCS` | `true` | Serve `/docs`, `/redoc`, `/openapi.json` |
| `LOG_LEVEL` | `info` | App and uvicorn log level |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies trusted for `X-Forwarded-*` |
| `PORT` | `8000` | Port inside the container (map it with `APP_PORT`) |
| `WEB_CONCURRENCY` | `1` | Uvicorn workers. **Keep 1:** the one-sync-at-a-time lock and sync status live in the process. For more capacity, give the container more CPU/memory |
| `KEEP_ALIVE_TIMEOUT` / `GRACEFUL_SHUTDOWN_TIMEOUT` | `5` / `30` | Seconds |

The container runs any other command through the same entrypoint, with the CA bundle set up. For example, `docker compose run --rm app python -c "import requests; print(requests.get('https://github.example.com').status_code)"` checks that your corporate CA is trusted.

---

## 9) API reference

Full interactive documentation: **<http://localhost:8000/docs>**.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/active-repositories` | Active repositories from `config.yaml` |
| GET | `/api/teams` | Teams and members configured in `config.yaml` |
| GET | `/api/config/thresholds` | Active metric thresholds |
| GET | `/api/team-performance` | Team-level PR metrics |
| GET | `/api/dora/team` | Team DORA-inspired metrics and weekly trends, including ghost merges |
| GET | `/api/developers` | Known developers |
| GET | `/api/developers/{username}` | Aggregate metrics for one developer |
| GET | `/api/developers/{username}/analytics` | Review analytics + AI/heuristic summary for one developer (all stored PRs) |
| GET | `/api/developers/{username}/dora` | DORA-inspired metrics for one developer |
| GET | `/api/chart/pr-cycle-by-developer` | Avg cycle time per developer |
| GET | `/api/chart/prs-per-week` | Merged PRs per ISO week; optional `?developer=<username>` |
| POST | `/api/ingest` | Start a GitHub sync in the background for PRs updated in a range. Body: `{"date_from": "<ISO>", "date_to": "<ISO>", "full_refresh": false}` (or `{"lookback_days": 7}`). Returns `202`, `400` for an invalid range, or `409` if one is already running |
| GET | `/api/ingest/status` | `running` or `idle`, plus `last_run` statistics (PRs updated, unchanged, GitHub requests, snapshot saved) |
| GET | `/api/snapshots` | Saved snapshots, newest first |
| POST | `/api/snapshots` | Save a snapshot of the current data |
| GET | `/api/branches` | Target branches of stored PRs with PR counts (`?date_from=`/`?date_to=`, `?team=`), plus PRs with no recorded branch |
| GET | `/api/people` | Configured people with their GitHub login, Azure DevOps identities and matched PR / work item counts, plus unlinked Azure identities |
| GET | `/api/devops/config` | Azure DevOps connection status (no secrets) and stored counts |
| POST | `/api/devops/sync` | Start an Azure DevOps sync for a range. Body: `{"date_from": "<ISO>", "date_to": "<ISO>"}` (or `{"lookback_days": 30}`). `400` if not configured or the range is invalid, `409` if a sync is running |
| GET | `/api/devops/sync/status` | `running` / `idle` plus `last_run` statistics |
| GET | `/api/devops/filters` | Tags (with counts), people (with role/team), work item types, teams |
| GET | `/api/devops/stories` | Filtered work items + summary + efficiency by person |
| GET | `/api/devops/dora` | DORA metrics for everyone, `?team=` or `?person=`, plus the pipeline runs behind them (`runs`, with `attributed_by`) |
| GET | `/api/devops/report` | Download `?format=csv` or `?format=markdown` for the filtered work items |
| POST | `/api/snapshots/load` | Load the newest snapshot (or `{"name": ...}`), upserting into the database |

Query parameters:

- `?date_from=…&date_to=…`: the range, i.e. PRs created or merged in `[date_from, date_to)`. Each takes an ISO date (`2026-10-01`; as `date_to` it covers that whole day) or a date-time (`2026-10-01T09:30:00Z`, `+05:30`; no offset means UTC). `date_to` defaults to now, and without `date_from` the range is the last `?days=N` days (default 30). Applies to `/api/team-performance`, `/api/dora/*`, `/api/developers/{username}/dora`, `/api/chart/*` and `/api/branches`. An invalid range returns `400`.
- DevOps endpoints take the same `date_from` / `date_to` (default the last 30 days), repeatable `tags` and `types`, `tag_mode=any|all`, `person`, `team` and `status=completed|all`.
- `?branches=<name>` (repeatable): only PRs targeting these branches. Applies to `/api/team-performance`, `/api/dora/team`, `/api/chart/*`, `/api/developers/{username}/dora`, `/api/developers/{username}/analytics`, `/api/devops/dora` and `/api/devops/report`.
- `?team=<TeamName>`: scopes the team-level endpoints (`/api/team-performance`, `/api/dora/team`, `/api/developers`, `/api/chart/*`) to one configured team. An unknown team returns `404`. Without it, they cover all configured teams, or everyone when no teams are configured.

> **Security note:** the API has **no authentication**. Anyone who can reach the port can read the metrics and trigger syncs (which use your GitHub token's rate limit). Run it on a trusted network or behind an authenticating reverse proxy.

---

## 10) What the metrics mean

This project only sees GitHub data (PRs, reviews, comments, commits), not deployments or incidents. So the delivery metrics are **GitHub-based proxies inspired by DORA**, not real DORA metrics.

| Metric | How it is calculated |
|---|---|
| **Merge frequency** | Merged PRs per week in the window (proxy for deployment frequency) |
| **Lead time (avg / median)** | PR created → merged |
| **Time to first review** | PR created → first review feedback from someone other than the author |
| **Review coverage** | % of PRs with at least one review or review comment |
| **Approval rate** | % of reviewed PRs with at least one approval |
| **Change-failure proxy** | % of merged PRs that received a "changes requested" review |
| **Ghost merges** | Merged PRs that nobody other than the author reviewed (no review from another person, so no approval either); count and % of merged PRs |
| **Recovery time** | For PRs with requested changes: last review feedback → merge |
| **Rework after feedback** | Commits pushed after the first review feedback |
| **First follow-up** | First review feedback → the author's next commit |
| **Comment themes** | Review comments sorted into style / correctness / testing / maintainability / performance / security by keyword |
| **Languages** | Taken from the file extensions of commented files |

The dashboard period is the **date range** in the header: metrics include PRs **created or merged** in that range. Merge-based figures (merges, lead time, ghost merges, weekly trends, PRs merged per week) count PRs **merged** in the window. The developer review analytics (Summary / AI Metrics / Reviewers tabs) use all stored PRs for that developer.

The **Provider** tag on a developer's card names the analyzer that produced the AI metrics: e.g. `Ollama (qwen2.5-coder:14b)`, `OpenAI (gpt-4o)`, `Gemini (gemini-1.5-pro)`, or `Rule-based (heuristic)`. It shows `..., partly heuristic` when the LLM left some fields empty and they were filled by the rule-based analyzer.

---

## 11) Project layout

```
app/
  main.py               FastAPI app: routes, background ingestion, SPA serving
  config.py             Settings (environment / .env)
  config_loader.py      Reads config.yaml (repositories)
  teams.py              Reads config.yaml teams, member filter, skipped-author log
  snapshot.py           Save / load data snapshots (gzipped JSON)
  azure_devops.py       Azure DevOps REST client (work items, pipeline runs)
  devops_config.py      azure_devops settings from config.yaml / .env
  devops_ingest.py      Azure DevOps sync (upsert, team filter)
  devops_metrics.py     Story filters, efficiency, DORA (DevLake definitions), reports
  database.py           Engine/session, automatic table + column + index creation
  models.py             SQLAlchemy models: Developer, Repository, PullRequest, Commit, PullRequestComment
  github_ingestion.py   GitHub GraphQL client (pagination, batching, rate limits)
  ingest.py             Upserts fetched data into PostgreSQL
  queries.py            Team / developer / chart queries
  time_window.py        Date/time ranges shared by the API filters and both syncs
  dora_metrics.py       DORA-inspired metrics and weekly trends
  developer_analytics.py  Per-developer review analytics + narrative
  ai_analyzers.py       Online LLM → Ollama → rule-based analyzer
frontend/               React + Vite dashboard (src/components: GitAnalyticsDashboard, DevOpsIntegrationPage,
                        DateTimeRangePicker + dateRange.ts, BranchSelect)
scripts/                seed_data.sql (demo data), seed_devops.sql (Azure demo data), clear_data.sql (wipe data)
tests/                  Backend unit tests
config.yaml             Repositories to analyse
snapshots/              Data snapshots (created by the app; git-ignored)
docker/entrypoint.sh    Container entrypoint: certificates (TLS, extra CAs), permissions, uvicorn settings, drops root
docker/healthcheck.py   Container healthcheck (HTTP or HTTPS)
certs/                  TLS certificate/key and extra CAs (git-ignored; see certs/README.md)
docker-compose.prod.yml Production overrides (HTTPS required, DB not published, limits)
Dockerfile              Multi-stage build: UI (Node) + API (Python) in one image
docker-compose.yml      PostgreSQL + app (+ optional Ollama)
```

---

## 12) Running the tests

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

## 13) Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits with `entrypoint: ERROR: ...` | The message names the problem: invalid CA file, certificate/key mismatch, wrong `TLS_KEY_PASSWORD`, or `REQUIRE_TLS` without a certificate (section 8.1) |
| `SSL: CERTIFICATE_VERIFY_FAILED` when syncing | A corporate proxy or self-hosted GitHub/Azure DevOps uses a private CA: put its certificate in `certs/ca/` and restart |
| Browser shows a certificate warning | The certificate isn't trusted by the browser (self-signed), or its name doesn't match the URL; use a certificate for the host name you open |
| Selecting a target branch hides older PRs | They were synced before target branches were recorded: run a GitHub sync with **Full refresh on sync** |
| DevOps page says "Not configured" | Set `AZURE_DEVOPS_PAT` in `.env` and `azure_devops.organization` / `projects` in `config.yaml`, then restart (section 7.2) |
| Azure sync fails with "rejected the credentials" | The PAT is expired, belongs to another organization, or lacks the **Work Items: Read** / **Build: Read** scopes |
| DORA shows "No production deployments" | `deployment_pattern` / `production_pattern` don't match your pipeline names; the page header shows the patterns in use |
| Filtering DevOps by a person shows few pipeline runs | Runs are attributed by *Requested for*, by the commits/PR refs they built, and by the deployments that shipped the person's PRs (section 7.4). Link the person's Azure identity (`azure_devops:` email or display name) under `teams`, and sync GitHub with **Full refresh on sync** once so PRs have merge commits. The *Why it is included* column shows how each run matched |
| A custom date range shows other times than expected | The picker uses your browser's time zone; the API and the database use UTC. Ranges include the start and exclude the end |
| A person's Azure work items appear under their Azure name instead of their configured `name` | Their Azure identity isn't linked: add it to their `azure_devops` list in `teams` (the **People & identities** panel lists unlinked identities) |
| A developer is missing after a sync | They aren't listed under `teams` in `config.yaml` (check the "not in any team" lines in the log), or their login is misspelled |
| Dashboard is empty | Click **⟳ Sync from GitHub**, or [load the sample data](#43-load-sample-demo-data). Check `config.yaml` lists the right repositories |
| Sync finishes but nothing appears | Check `docker compose logs app`. *"GitHub rejected the request (401/403)"* means the token is invalid or lacks access to the repository. Also try a wider date range: only PRs updated in that range are imported |
| App container exits with a `github_token` / `database_url` "Field required" error | `GITHUB_TOKEN` is missing from `.env` (or `DATABASE_URL` when running without Docker) |
| `password authentication failed` after changing `POSTGRES_PASSWORD` | The password is fixed when the volume is first created. Run `docker compose down -v` (deletes data), then start again |
| `port is already allocated` | Set `APP_PORT` or `POSTGRES_PORT` in `.env` |
| `Could not create unique index ...` warning on startup | An older version stored duplicate PRs. Run `scripts/clear_data.sql` and sync again |
| Logs say `LocalOllamaHandler returned no usable analysis` | The model answered without usable values (e.g. all `null`), so the rule-based analysis is used. Partial answers are kept, and missing fields are filled from the rule-based analysis (logged as `... filled from the heuristic analysis`). Small models do this more often; try a larger `OLLAMA_MODEL` or Ollama 0.5+, which supports the JSON schema the app sends |
| `exec /usr/local/bin/entrypoint.sh: no such file or directory` | The script had Windows (CRLF) line endings. The image now strips them, so rebuild with `docker compose up --build`. `.gitattributes` keeps `*.sh` files LF on new checkouts |
| `Could not write snapshot: Permission denied` | The container fixes `./snapshots` ownership at startup; if your setup blocks that (e.g. rootless Docker or SELinux), run `chmod 777 snapshots` on the host, or use `:z` on the volume under SELinux |
| Logs say `Ollama is not reachable` | See [AI analysis](#6-ai-analysis-optional). From Docker, Ollama on the host must be reachable at `host.docker.internal:11434` (on Linux start it with `OLLAMA_HOST=0.0.0.0`) |
| UI on port 5173 shows "Failed to load data" | The backend isn't running on port 8000, or set `VITE_API_PROXY_TARGET` |
