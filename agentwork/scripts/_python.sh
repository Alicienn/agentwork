# Sourced by the agentwork launchers: picks a Python 3.9+ interpreter into AW_PY.
# Override with PYTHON_BIN=/path/to/python.
AW_PY=""
if [ -n "${PYTHON_BIN:-}" ]; then
  AW_PY="$PYTHON_BIN"
else
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      AW_PY="$c"
      break
    fi
  done
fi
if [ -z "$AW_PY" ]; then
  echo "ERROR: Python 3.9+ not found (tried python3, python). Set PYTHON_BIN." >&2
  exit 127
fi
