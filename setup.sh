#!/usr/bin/env sh
# Thin launcher — the logic lives in agentwork/pipeline.py (setup). Works on Linux and macOS.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/agentwork/scripts/_python.sh"
exec "$AW_PY" "$HERE/agentwork/pipeline.py" setup "$@"
