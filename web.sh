#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi
exec "$PY" -m spot_history web --host 0.0.0.0 --port 8345 "$@"
