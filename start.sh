#!/bin/sh
# Production entrypoint (Railway): the seat poller in the background, the website in front.
# Both share the SQLite file at $SNATCH_DB (put it on a persistent volume, e.g. /data/snatch.db).
set -e
export PYTHONUNBUFFERED=1

if [ -n "$SNATCH_DB" ]; then
  mkdir -p "$(dirname "$SNATCH_DB")"
fi

# Poller: reads SNATCH_TERMS / SNATCH_DB / POLL_EVERY. Restarts itself if it ever exits.
(
  while true; do
    python -m olemiss_snatch.poll || true
    echo "poller exited; restarting in 30s"
    sleep 30
  done
) &

exec gunicorn olemiss_snatch.web:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 --threads 4 --timeout 60 \
  --access-logfile -
