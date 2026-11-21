#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi
"$PY" -m spot_history google import "$@"
"$PY" -m spot_history google osm
"$PY" -m spot_history index build
