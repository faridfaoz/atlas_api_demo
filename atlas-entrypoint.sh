#!/bin/bash
# Graceful start/stop wrapper for the Apache Atlas lab container.
#
# Fix for crash-loop issue: the stock image entrypoint
#   (/bin/bash -c 'atlas_start.py; tail -fF application.log')
# never forwards SIGTERM, so 'docker stop' SIGKILLs the embedded
# HBase/ZooKeeper/Kafka processes. The persisted ZooKeeper state then
# keeps a stale Kafka ephemeral node (/brokers/ids/1) whose owner session
# no longer matches, and Atlas crash-loops on every subsequent start with:
#   "Error while creating ephemeral at /brokers/ids/1, node already exists
#    and owner ... does not match current session ..."
#
# This wrapper traps SIGTERM/SIGINT and runs atlas_stop.py, which stops
# Atlas, Solr, HBase and ZooKeeper cleanly (closing ZK sessions removes
# the stale ephemeral nodes), making restarts safe.

ATLAS_HOME=/opt/apache-atlas-2.1.0
ATLAS_LOG="${ATLAS_HOME}/logs/application.log"

stop_handler() {
  echo "[atlas-lab-entrypoint] SIGTERM/SIGINT received - stopping Atlas stack gracefully..."
  python "${ATLAS_HOME}/bin/atlas_stop.py" || true
  echo "[atlas-lab-entrypoint] Atlas stack stopped."
  exit 0
}

trap stop_handler TERM INT

echo "[atlas-lab-entrypoint] starting Atlas stack..."
# -u: python block-buffers stdout on pipes, which would hide the
# "Apache Atlas Server process started!" marker from 'docker logs'
python -u "${ATLAS_HOME}/bin/atlas_start.py" &
STARTER_PID=$!

mkdir -p "$(dirname "$ATLAS_LOG")"
touch "$ATLAS_LOG"
tail -F "$ATLAS_LOG" &
TAIL_PID=$!

# Exit (so the restart policy can re-trigger) as soon as the starter
# exits, i.e. Atlas died, instead of tailing logs forever like the
# stock entrypoint does.
wait -n
EXIT_CODE=$?
kill "$TAIL_PID" 2>/dev/null
exit $EXIT_CODE
