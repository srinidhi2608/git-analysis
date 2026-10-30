#!/bin/sh
# =============================================================================
# Container entrypoint — Git Analysis
#
# Runs as root only to prepare the container, then drops to `appuser`:
#   1. Makes the bind-mounted snapshot directory writable.
#   2. Trusts extra CA certificates from $CA_CERTS_DIR (corporate proxy,
#      GitHub Enterprise, Azure DevOps Server, internal LLM endpoints, ...).
#   3. Enables HTTPS when $TLS_CERT_FILE and $TLS_KEY_FILE exist (optionally
#      mutual TLS with $TLS_CLIENT_CA_FILE).
#   4. Starts uvicorn with production settings ("serve", the default command),
#      or runs any other command given to the container.
#
# Certificates come from /app/certs: copied into the image by the Dockerfile
# from ./certs, or mounted at runtime (docker-compose mounts ./certs read-only).
# =============================================================================
set -eu

log() { echo "entrypoint: $*" >&2; }
fail() { log "ERROR: $*"; exit 1; }
is_true() { case "$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac; }

APP_USER="${APP_USER:-appuser}"
RUN_DIR="${RUN_DIR:-/app/run}"
CA_CERTS_DIR="${CA_CERTS_DIR:-/app/certs/ca}"
TLS_CERT_FILE="${TLS_CERT_FILE:-/app/certs/tls/server.crt}"
TLS_KEY_FILE="${TLS_KEY_FILE:-/app/certs/tls/server.key}"
TLS_CLIENT_CA_FILE="${TLS_CLIENT_CA_FILE:-/app/certs/tls/client-ca.crt}"
TLS_ENABLED="${TLS_ENABLED:-auto}"           # auto | true | false
SYSTEM_CA_BUNDLE="${SYSTEM_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"

if [ "$(id -u)" = "0" ]; then
    AS_ROOT=1
else
    AS_ROOT=0
fi

mkdir -p "$RUN_DIR" /app/snapshots
if [ "$AS_ROOT" = "1" ]; then
    chown -R "$APP_USER:$APP_USER" /app/snapshots || log "warning: could not change ownership of /app/snapshots"
    chown "$APP_USER:$APP_USER" "$RUN_DIR"
    chmod 0750 "$RUN_DIR"
fi

# --- 1. Extra trusted CA certificates ----------------------------------------
# A combined bundle (system CAs + yours) is written to $RUN_DIR and exported for
# requests (REQUESTS_CA_BUNDLE), httpx/OpenSSL (SSL_CERT_FILE) and curl.
ca_files=""
if [ -d "$CA_CERTS_DIR" ]; then
    for file in "$CA_CERTS_DIR"/*.crt "$CA_CERTS_DIR"/*.pem; do
        [ -f "$file" ] || continue
        python3 - "$file" <<'PY' || fail "$file is not a valid PEM certificate"
import ssl, sys
ssl.create_default_context(cafile=sys.argv[1])
PY
        ca_files="$ca_files $file"
    done
fi
if [ -n "$ca_files" ]; then
    bundle="$RUN_DIR/ca-bundle.pem"
    cat "$SYSTEM_CA_BUNDLE" > "$bundle"
    for file in $ca_files; do
        printf '\n# %s\n' "$(basename "$file")" >> "$bundle"
        cat "$file" >> "$bundle"
        log "trusting extra CA certificate $(basename "$file")"
    done
    chmod 0644 "$bundle"
    export SSL_CERT_FILE="$bundle" REQUESTS_CA_BUNDLE="$bundle" CURL_CA_BUNDLE="$bundle"
fi

# --- 2. HTTPS ----------------------------------------------------------------
tls_args=""
scheme="http"
mtls=0
if is_true "$TLS_ENABLED" || { [ "$TLS_ENABLED" = "auto" ] && [ -f "$TLS_CERT_FILE" ] && [ -f "$TLS_KEY_FILE" ]; }; then
    [ -f "$TLS_CERT_FILE" ] || fail "TLS is enabled but TLS_CERT_FILE ($TLS_CERT_FILE) does not exist"
    [ -f "$TLS_KEY_FILE" ] || fail "TLS is enabled but TLS_KEY_FILE ($TLS_KEY_FILE) does not exist"

    # The key may be mounted root-only (0600); copy it where only the app user can read it.
    tls_dir="$RUN_DIR/tls"
    mkdir -p "$tls_dir"
    cp "$TLS_CERT_FILE" "$tls_dir/server.crt"
    cp "$TLS_KEY_FILE" "$tls_dir/server.key"
    chmod 0444 "$tls_dir/server.crt"
    chmod 0400 "$tls_dir/server.key"
    if [ "$AS_ROOT" = "1" ]; then
        chown -R "$APP_USER:$APP_USER" "$tls_dir"
    fi
    chmod 0500 "$tls_dir"

    python3 - "$tls_dir/server.crt" "$tls_dir/server.key" <<'PY' || fail "TLS certificate and key do not match, or the key password (TLS_KEY_PASSWORD) is wrong"
import os, ssl, sys
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(sys.argv[1], sys.argv[2], password=os.environ.get("TLS_KEY_PASSWORD") or None)
PY
    tls_args="--ssl-certfile $tls_dir/server.crt --ssl-keyfile $tls_dir/server.key"
    if [ -n "${TLS_KEY_PASSWORD:-}" ]; then
        # Passed via the environment-backed option below, not on the command line.
        export UVICORN_SSL_KEYFILE_PASSWORD="$TLS_KEY_PASSWORD"
    fi
    if [ -f "$TLS_CLIENT_CA_FILE" ]; then
        cp "$TLS_CLIENT_CA_FILE" "$tls_dir/client-ca.crt"
        chmod 0444 "$tls_dir/client-ca.crt"
        # CERT_REQUIRED (2): clients must present a certificate signed by this CA (mutual TLS).
        tls_args="$tls_args --ssl-ca-certs $tls_dir/client-ca.crt --ssl-cert-reqs 2"
        mtls=1
        log "mutual TLS enabled: client certificates must be signed by $(basename "$TLS_CLIENT_CA_FILE")"
    fi
    scheme="https"
    [ "${mtls:-0}" = "1" ] && scheme="https-mtls"
    log "HTTPS enabled with $(basename "$TLS_CERT_FILE")"
elif is_true "${REQUIRE_TLS:-false}"; then
    fail "REQUIRE_TLS is set but no certificate/key was found at $TLS_CERT_FILE / $TLS_KEY_FILE"
fi
# The healthcheck reads the scheme to probe the right URL.
echo "$scheme" > "$RUN_DIR/scheme"

# --- 3. Command ---------------------------------------------------------------
if [ "${1:-serve}" = "serve" ]; then
    [ $# -gt 0 ] && shift
    workers="${WEB_CONCURRENCY:-1}"
    if [ "$workers" != "1" ]; then
        log "warning: WEB_CONCURRENCY=$workers. Sync status and the one-sync-at-a-time lock are per process; keep 1 worker unless syncs are triggered from one place only."
    fi
    # shellcheck disable=SC2086  # tls_args is intentionally word-split
    set -- uvicorn app.main:app \
        --host "${HOST:-0.0.0.0}" \
        --port "${PORT:-8000}" \
        --workers "$workers" \
        --proxy-headers \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
        --log-level "$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')" \
        --timeout-keep-alive "${KEEP_ALIVE_TIMEOUT:-5}" \
        --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_TIMEOUT:-30}" \
        --no-server-header \
        $tls_args \
        "$@"
    log "starting $scheme server on port ${PORT:-8000} as $APP_USER"
fi

if [ "$AS_ROOT" = "1" ]; then
    exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups "$@"
fi
exec "$@"
