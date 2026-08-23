# Git Analysis

This project provides:
- A FastAPI backend for Git analytics endpoints
- A React dashboard component at `frontend/src/components/GitAnalyticsDashboard.tsx`

## 1) Prerequisites

- Python 3.11+  
- Node.js 18+ and npm  
- PostgreSQL running locally or remotely  
- GitHub Personal Access Token (for ingestion use cases)

## 2) Required properties to set **before start**

Create `.env` in the project root directory:

```env
DATABASE_URL=postgresql://<db_user>:<db_password>@localhost:5432/git_analysis
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Update `config.yaml` in the project root with repositories:

```yaml
repositories:
  - name: "owner/repo-name"
    is_active: true
```

Required property values:
- `DATABASE_URL`: valid PostgreSQL SQLAlchemy connection string
- `GITHUB_TOKEN`: token with access to read repository PR data
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

Backend should start at:
- `http://localhost:8000`

## 4) What to test (screens/endpoints)

Open these in browser to confirm backend is working:

1. `http://localhost:8000/`  
   Expected: `{"message":"Git Analysis API is running"}`
2. `http://localhost:8000/docs`  
   Expected: Swagger UI opens
3. `http://localhost:8000/active-repositories`  
   Expected: returns active repos from `config.yaml`
4. `http://localhost:8000/api/team-performance`
5. `http://localhost:8000/api/developers/<github_username>`

## 5) Dashboard UI setup and test screens (this repository)

### 5.1 Install frontend dependencies

From the project root:

```bash
cd frontend
npm install
```

> If you see `'vite' is not recognized`, dependencies are not installed yet. Run `npm install` in `frontend` first.

### 5.2 Start the React app

From `frontend`:

```bash
npm run dev
```

Alternative:

```bash
npm start
```

Open `http://localhost:5173` (or the URL shown in terminal).

### 5.3 What to verify on screen

1. Page loads with title: **Engineering Performance Dashboard**
2. Two tabs are visible:
   - **Team Overview**
   - **Individual Performance**
3. **Team Overview** tab checks:
   - 3 KPI cards are visible
   - Bar chart is visible with title **Average PR Cycle Time by Developer**
   - Line chart is visible with title **Total PRs Merged per Week**
4. Switch to **Individual Performance** tab:
   - KPI cards update
   - First chart title changes to **Average PR Cycle Time Trend**
   - Second chart still shows **Total PRs Merged per Week**
5. Hover over chart points/bars and confirm tooltips appear.
