#!/bin/sh
# Make the bind-mounted snapshot directory writable for the app user, then drop
# root privileges before starting the application.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/snapshots
    chown -R appuser:appuser /app/snapshots || echo "warning: could not change ownership of /app/snapshots" >&2
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi
exec "$@"
