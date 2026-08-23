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

## 5) Dashboard component test screens

The dashboard component includes two UI screens (tabs):
- **Team Overview**
- **Individual Performance**

To test the dashboard UI, mount `GitAnalyticsDashboard` in your React app and run that app (for example with `npm run dev` or `npm start` in your React project).  
Verify:
- Tab switch between **Team Overview** and **Individual Performance**
- Bar chart renders: **Average PR Cycle Time by Developer**
- Line chart renders: **Total PRs Merged per Week**
