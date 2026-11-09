#!/usr/bin/env sh
# Thin launcher — the logic lives in agentwork/pipeline.py (driver). Works on Linux and macOS.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/../scripts/_python.sh"
exec "$AW_PY" "$HERE/../pipeline.py" driver "$@"
