#!/bin/sh
# Safe update for the Docker/Synology deployment.
# Pulls the new image, creates a backup, rebuilds the index and restarts the service.
set -eu

cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

$COMPOSE pull
$COMPOSE stop spot-history-manager || true
$COMPOSE run --rm --no-deps spot-history-manager \
    python -m spot_history backup --keep-days 3
$COMPOSE run --rm --no-deps spot-history-manager \
    python -m spot_history index build
$COMPOSE up -d
