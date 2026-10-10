# =============================================================================
# Multi-stage Dockerfile — Git Analysis
#
# Stage 1 (node-builder): builds the React/TypeScript frontend
# Stage 2 (runtime):      installs Python deps and serves the API and the built
#                         UI from one FastAPI + uvicorn process on port 8000
# =============================================================================

# ─── Stage 1: build the React frontend ───────────────────────────────────────
# Vite 8 requires Node.js 20.19+ or 22.12+.
FROM node:22-alpine AS node-builder

WORKDIR /build/frontend

# Install JS dependencies first (cached layer)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy the rest of the frontend source and build
COPY frontend/ ./
RUN npm run build
# Output is now at /build/frontend/dist


# ─── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python dependencies (cached layer — only rebuilds when requirements change).
# psycopg2-binary bundles libpq, so no system packages are needed.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy application source code and the default repository list
COPY app/ ./app/
COPY config.yaml ./

# Copy the built React app so FastAPI can serve it
COPY --from=node-builder /build/frontend/dist ./frontend/dist/

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# DATABASE_URL and GITHUB_TOKEN must be supplied at runtime as environment
# variables (docker compose wires these up from .env).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
