# =============================================================================
# Multi-stage Dockerfile — Git Analysis
#
# Stage 1 (node-builder): builds the React/TypeScript frontend
# Stage 2 (runtime):      installs Python deps and serves everything via
#                         FastAPI + uvicorn (no separate web server needed)
# =============================================================================

# ─── Stage 1: build the React frontend ───────────────────────────────────────
FROM node:20-alpine AS node-builder

WORKDIR /build/frontend

# Install JS dependencies first (cached layer)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

# Copy the rest of the frontend source and build
COPY frontend/ ./
RUN npm run build
# Output is now at /build/frontend/dist


# ─── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# System deps: libpq is required by psycopg2-binary
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (cached layer — only rebuilds when requirements change)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY config.yaml ./

# Copy the built React app so FastAPI can serve it
COPY --from=node-builder /build/frontend/dist ./frontend/dist/

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# DATABASE_URL and GITHUB_TOKEN must be supplied at runtime via environment
# variables or a .env file (mounted as a volume or passed via docker-compose).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
