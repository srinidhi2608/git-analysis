"""Docker HEALTHCHECK: probe /health over HTTP or HTTPS (as set up by entrypoint.sh)."""

import os
import socket
import ssl
import sys
import urllib.request

run_dir = os.environ.get("RUN_DIR", "/app/run")
try:
    with open(os.path.join(run_dir, "scheme")) as handle:
        scheme = handle.read().strip() or "http"
except OSError:
    scheme = "http"

port = os.environ.get("PORT", "8000")
if scheme == "https-mtls":
    # Mutual TLS: the probe has no client certificate, so only check that the port accepts connections.
    try:
        socket.create_connection(("127.0.0.1", int(port)), timeout=4).close()
        sys.exit(0)
    except OSError as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        sys.exit(1)

context = None
if scheme == "https":
    # The certificate is issued for the public host name, not 127.0.0.1.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

try:
    with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/health", timeout=4, context=context) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f"healthcheck failed: {exc}", file=sys.stderr)
    sys.exit(1)
