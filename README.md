# Git Analysis

This project provides:
- A FastAPI backend for Git analytics endpoints
- A React dashboard component at `frontend/src/components/GitAnalyticsDashboard.tsx`

## 1) Prerequisites

- Python 3.11+  
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

## 5) Dashboard component test screens (React app)

This repository contains the component at `frontend/src/components/GitAnalyticsDashboard.tsx`, but does not include a full React app shell.  
Use the steps below in your React app to test it.

### 5.1 Add dependencies

In your React project directory:

```bash
npm install recharts
```

Make sure Tailwind CSS is already configured in your React app.

### 5.2 Add the dashboard component

1. Copy `frontend/src/components/GitAnalyticsDashboard.tsx` into your React app (for example: `src/components/GitAnalyticsDashboard.tsx`).
2. Import and render it from your app entry screen (for example in `src/App.tsx`):
   - render `<GitAnalyticsDashboard />`

### 5.3 Start the React app

From your React project root:

```bash
npm run dev
```

If your project uses Create React App, use:

```bash
npm start
```

Open the local URL printed in terminal (commonly `http://localhost:5173` for Vite or `http://localhost:3000` for CRA).

### 5.4 What to verify on screen

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
