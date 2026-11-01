#!/usr/bin/env bash
# Autonomous Driver Launcher & Supervisor
#
# Usage:
#   ./agentwork/scripts/driver.sh [claude|claude-or|codex|agy|gemini|claude-local|pi-code|auto] [--build-all] [extra args...]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_YAML="$REPO/agentwork/config/project.yaml"

if [ ! -f "$PROJECT_YAML" ]; then
  echo "ERROR: $PROJECT_YAML not found. Run setup.sh first." >&2
  exit 1
fi

ROUTING_PROFILE="$(grep -E '^[[:space:]]*routing_profile:' "$PROJECT_YAML" | head -n1 | awk '{print $2}' | tr -d '"'"'" || echo "")"
DEFAULT_DRIVER="$(grep -E '^[[:space:]]*default_driver:' "$PROJECT_YAML" | head -n1 | awk '{print $2}' | tr -d '"'"'" || echo "auto")"

if [ -z "$ROUTING_PROFILE" ] || [ "$ROUTING_PROFILE" = "<REQUIRED>" ]; then
  echo "POLICY ERROR: routing_profile is not set in $PROJECT_YAML." >&2
  echo "Specify a profile via: setup.sh . --profile <openrouter-first|sensitive|balanced|local-only>" >&2
  exit 2
fi

DRIVER="$DEFAULT_DRIVER"
BUILD_ALL=0

# Parse arguments
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --build-all) BUILD_ALL=1 ;;
    claude|claude-or|codex|agy|gemini|claude-local|pi-code|auto) DRIVER="$arg" ;;
    *) ARGS+=("$arg") ;;
  esac
done

# If --build-all, supervise pipeline.py drive directly without launching an LLM driver
if [ "$BUILD_ALL" = "1" ]; then
  echo "=== Starting Autonomous Build Supervisor (Profile: $ROUTING_PROFILE) ==="
  PIPELINE="$REPO/agentwork/pipeline.py"
  [ -f "$PIPELINE" ] || { echo "ERROR: $PIPELINE not found" >&2; exit 1; }

  CAFFEINATE=""
  if command -v caffeinate >/dev/null 2>&1; then
    CAFFEINATE="caffeinate -dimsu"
  fi

  echo "Launching: python3 $PIPELINE drive"
  exec $CAFFEINATE python3 "$PIPELINE" drive
fi

# Resolve auto driver based on profile and installed CLIs
pick_installed() {
  for cand in "$@"; do
    local bin="$cand"
    [ "$cand" = "pi-code" ] && bin="pi"
    if command -v "$bin" >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  echo "$1"
}

if [ "$DRIVER" = "auto" ]; then
  case "$ROUTING_PROFILE" in
    openrouter-first) DRIVER="$(pick_installed claude-or claude codex agy gemini)" ;;
    sensitive|balanced) DRIVER="$(pick_installed claude codex agy gemini)" ;;
    local-only) DRIVER="$(pick_installed claude-local pi-code)" ;;
    *) DRIVER="$(pick_installed claude codex agy gemini)" ;;
  esac
fi

# Hard policy enforcement before launching any driver
if [ "$ROUTING_PROFILE" = "sensitive" ]; then
  case "$DRIVER" in
    claude-or*|*openrouter*)
      echo "SECURITY ERROR: '$DRIVER' is strictly forbidden under profile 'sensitive'." >&2
      exit 2
      ;;
  esac
elif [ "$ROUTING_PROFILE" = "local-only" ]; then
  case "$DRIVER" in
    claude-local|pi-code) ;;
    *)
      echo "SECURITY ERROR: Cloud driver '$DRIVER' is strictly forbidden under profile 'local-only'." >&2
      exit 2
      ;;
  esac
fi

echo "Launching Driver: $DRIVER (Profile: $ROUTING_PROFILE)"
KICKOFF_PROMPT="Read AGENTS.md, agentwork/state/PROJECT.md, and docs/design/DESIGN.md. Then execute following Bootstrap an Autonomous Multi-Agent Repository.md."

case "$DRIVER" in
  claude)
    exec claude --dangerously-skip-permissions "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  claude-or)
    exec claude-or --dangerously-skip-permissions "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  codex)
    exec codex --dangerously-bypass-approvals-and-sandbox "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  agy)
    exec agy --dangerously-skip-permissions "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  gemini)
    exec gemini -y "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  claude-local)
    exec claude-local --permission-mode bypassPermissions "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  pi-code)
    exec pi --provider "${PI_PROVIDER:-ollama}" --model pi-code "$KICKOFF_PROMPT" ${ARGS+"${ARGS[@]}"}
    ;;
  *)
    echo "ERROR: Unknown driver '$DRIVER'" >&2
    exit 1
    ;;
esac
