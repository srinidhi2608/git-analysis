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

# Certificates (see certs/README.md). Everything placed under ./certs is copied
# into the image; at runtime docker-compose mounts ./certs over it read-only, so
# certificates can also be rotated without rebuilding. Private keys are kept
# root-only; the entrypoint hands the app user its own copy.
COPY certs/ /app/certs/

# Entrypoint + healthcheck. Strip Windows line endings (a CRLF checkout breaks
# the "#!/bin/sh" line with "exec ... no such file or directory").
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/healthcheck.py /usr/local/bin/healthcheck.py
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && \
    chmod 0755 /usr/local/bin/entrypoint.sh && \
    chmod 0644 /usr/local/bin/healthcheck.py && \
    adduser --disabled-password --gecos "" --uid 1000 appuser && \
    # Application code stays root-owned (read-only for the app user); only
    # snapshots and the runtime directory are writable.
    mkdir -p /app/snapshots /app/run && \
    chown appuser:appuser /app/snapshots /app/run && \
    chown -R root:root /app/certs && \
    find /app/certs -type d -exec chmod 0755 {} + && \
    find /app/certs -type f -exec chmod 0600 {} +

ENV HOST=0.0.0.0 \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    LOG_LEVEL=info \
    TLS_ENABLED=auto \
    TLS_CERT_FILE=/app/certs/tls/server.crt \
    TLS_KEY_FILE=/app/certs/tls/server.key \
    TLS_CLIENT_CA_FILE=/app/certs/tls/client-ca.crt \
    CA_CERTS_DIR=/app/certs/ca

# The entrypoint starts as root only to prepare certificates and permissions,
# then runs the app as `appuser` (see docker/entrypoint.sh).
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "/usr/local/bin/healthcheck.py"]

# "serve" starts uvicorn with production settings (HTTPS when certificates are
# present). Any other command runs as-is, e.g. `docker compose run app python -m ...`.
# DATABASE_URL and GITHUB_TOKEN must be supplied at runtime (docker compose wires
# these up from .env).
CMD ["serve"]
