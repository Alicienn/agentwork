#!/usr/bin/env bash
# Launch a coding agent in an isolated git worktree with per-attempt environment
# and optional Darwin Seatbelt or Linux Bubblewrap confinement.
#
# Usage:
#   launch.sh <WP-ID> <agent> [--dry-run] [--retry-note-file <path>] [--selftest]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  echo "Usage: launch.sh <WP-ID> <agent> [--dry-run] [--retry-note-file <file>] [--selftest]"
  exit 1
}

# Read project configuration early so selftest can validate the active isolation_mode
PROJECT_YAML="$REPO/agentwork/config/project.yaml"
ROUTING_PROFILE="openrouter-first"
ISOLATION_MODE="worktree-seatbelt"
if [ -f "$PROJECT_YAML" ]; then
  RP="$(grep -E '^[[:space:]]*routing_profile:' "$PROJECT_YAML" | head -n1 | awk '{print $2}' | tr -d '"'"'" || true)"
  [ -n "$RP" ] && ROUTING_PROFILE="$RP"
  IM="$(grep -E '^[[:space:]]*isolation_mode:' "$PROJECT_YAML" | head -n1 | awk '{print $2}' | tr -d '"'"'" || true)"
  [ -n "$IM" ] && ISOLATION_MODE="$IM"
fi

# Run selftest if requested
if [ "${1:-}" = "--selftest" ]; then
  echo "=== Running Sandbox Selftest (isolation_mode: $ISOLATION_MODE) ==="
  TEST_DIR="$(mktemp -d -t sb_selftest_XXXXXX)"
  trap 'rm -rf "$TEST_DIR"' EXIT

  touch "$TEST_DIR/allowed.txt" && echo "PASS: write to permitted temp"

  if [ "$ISOLATION_MODE" = "worktree-seatbelt" ]; then
    if command -v sandbox-exec >/dev/null 2>&1; then
      PROFILE="(version 1) (allow default) (deny file-write* (subpath \"$TEST_DIR/denied\"))"
      mkdir -p "$TEST_DIR/denied"
      if sandbox-exec -p "$PROFILE" touch "$TEST_DIR/denied/fail.txt" 2>/dev/null; then
        echo "FAIL: Seatbelt failed to block write to denied directory"
        exit 1
      else
        echo "PASS: Seatbelt successfully blocked write to denied directory"
      fi
    elif command -v bwrap >/dev/null 2>&1; then
      echo "PASS: Bubblewrap (bwrap) available for Linux confinement"
    else
      echo "FAIL: isolation_mode is 'worktree-seatbelt' but neither 'sandbox-exec' nor 'bwrap' is available." >&2
      exit 1
    fi
  else
    echo "PASS: worktree-only isolation mode selected"
  fi
  echo "=== Selftest complete ==="
  exit 0
fi

[ $# -ge 2 ] || usage
WP_ID="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
AGENT="$2"
shift 2

DRY=""
RETRY_NOTE_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY="--dry-run"; shift ;;
    --stream|--no-stream) shift ;;
    --retry-note-file) RETRY_NOTE_FILE="$2"; shift 2 ;;
    *) echo "ERROR: unknown option $1" >&2; exit 1 ;;
  esac
done

COMMON_GIT="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$REPO/.git")"
BASE_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "")"

# Enforce profile provider restrictions
if [ "$ROUTING_PROFILE" = "sensitive" ]; then
  case "$AGENT" in
    claude-or*|pi-fw*|*openrouter*)
      echo "SECURITY ERROR: Agent '$AGENT' is strictly blocked under routing_profile 'sensitive'." >&2
      exit 2
      ;;
  esac
elif [ "$ROUTING_PROFILE" = "local-only" ]; then
  case "$AGENT" in
    claude-local|pi-code|pi-devstral|fake) ;;
    *)
      echo "SECURITY ERROR: Cloud agent '$AGENT' is strictly blocked under routing_profile 'local-only'." >&2
      exit 2
      ;;
  esac
fi

find_task_spec() {
  local tid="$1"
  for d in "$REPO/agentwork/packages" "$REPO/agentwork/steps"; do
    [ -d "$d" ] || continue
    for f in "$d"/*.md; do
      [ -f "$f" ] || continue
      local base
      base="$(basename "$f")"
      case "$base" in
        *-template.md|WP-template.md|*-SPLIT.md) continue ;;
      esac
      local prefix="${base%%-*}"
      local stem="${base%.md}"
      if [ "$(printf '%s' "$prefix" | tr '[:lower:]' '[:upper:]')" = "$tid" ] || \
         [ "$(printf '%s' "$stem" | tr '[:lower:]' '[:upper:]')" = "$tid" ]; then
        echo "$f"
        return 0
      fi
    done
  done
  return 1
}

PKG="$(find_task_spec "$WP_ID" || true)"
[ -n "$PKG" ] || { echo "ERROR: no spec found for task ID $WP_ID" >&2; exit 1; }
PKG_NAME="$(basename "$PKG" .md)"

TREES="${AGENT_TREES:-$(dirname "$REPO")/$(basename "$REPO")-agent-trees}"
BRANCH="wp/$(printf '%s' "$WP_ID" | tr '[:upper:]' '[:lower:]')"
WT="$TREES/$WP_ID"

# Read tier and select brief
TIER="$(sed -n 's/^Tier:[[:space:]]*\([a-z-]*\).*/\1/p' "$PKG" | head -n1 || true)"
[ -n "$TIER" ] || TIER="code"
case "$TIER" in
  standard) TIER="code" ;;
  small)    TIER="mini-code" ;;
esac

BRIEF="$REPO/agentwork/CONTEXT.md"
if [ "$TIER" = "mini-code" ] && [ -f "$REPO/agentwork/CONTEXT-lite.md" ]; then
  BRIEF="$REPO/agentwork/CONTEXT-lite.md"
fi

emit_prompt() {
  cat "$BRIEF"
  printf '\n\n---\n\n'
  cat "$PKG"
  if command -v python3 >/dev/null 2>&1 && [ -f "$REPO/agentwork/pipeline.py" ]; then
    SYM_CTX="$(python3 "$REPO/agentwork/pipeline.py" context "$WP_ID" --symbols-only 2>/dev/null || true)"
    if [ -n "$SYM_CTX" ]; then
      printf '\n\n---\n# AUTO-EXTRACTED INTERFACE & DEPENDENCY SIGNATURES\n\n%s\n' "$SYM_CTX"
    fi
  fi
  if [ -n "$RETRY_NOTE_FILE" ] && [ -f "$RETRY_NOTE_FILE" ]; then
    printf '\n\n---\n# PREVIOUS ATTEMPT FAILURE / REPAIR REQUIRED\n\n'
    cat "$RETRY_NOTE_FILE"
  fi
  printf '\n\n---\nExecution: you are working in worktree %s on branch %s.\n' "$WT" "$BRANCH"
  printf 'Touch ONLY the files listed in Owns / owns:, commit locally, never push, and write agentwork/reports/%s.md when done.\n' "$(printf '%s' "$WP_ID" | tr '[:upper:]' '[:lower:]')"
  printf 'If this package is blocked by an unstated requirement ambiguity, write a ## Question section in your report.\n'
}

if [ "$DRY" = "--dry-run" ]; then
  echo "package: $PKG_NAME | agent: $AGENT | tier: $TIER | profile: $ROUTING_PROFILE | (dry run)"
  emit_prompt | awk 'NR<=40'
  exit 0
fi

# Always recreate worktree cleanly from current BASE_SHA so retries never inherit stale diffs
mkdir -p "$TREES"
mkdir -p "$COMMON_GIT/refs/heads/wp" "$COMMON_GIT/logs/refs/heads/wp"

if [ -d "$WT" ]; then
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
  git -C "$REPO" worktree prune >/dev/null 2>&1 || true
fi
if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$REPO" branch -D "$BRANCH" >/dev/null 2>&1 || true
fi
git -C "$REPO" worktree add -b "$BRANCH" "$WT" "$BASE_SHA"
echo "worktree: created fresh $WT on $BRANCH (base $BASE_SHA)"

export GIT_OPTIONAL_LOCKS=0
export GIT_AUTHOR_NAME="agentwork"
export GIT_AUTHOR_EMAIL="agentwork@local"
export GIT_COMMITTER_NAME="agentwork"
export GIT_COMMITTER_EMAIL="agentwork@local"

# Pre-seed files if package shipped a .files directory, commit them as the baseline commit, and advance BASE_SHA
SEED_DIR="$REPO/agentwork/packages/$PKG_NAME.files"
if [ -d "$SEED_DIR" ]; then
  ( cd "$SEED_DIR" && find . -type f -print0 | while IFS= read -r -d '' f; do
      mkdir -p "$WT/$(dirname "$f")" && cp "$f" "$WT/$f"
    done )
  if [ -n "$(git -C "$WT" status --porcelain 2>/dev/null || true)" ]; then
    git -C "$WT" add -A
    git -C "$WT" commit -q -m "chore(seed): pre-seed files for $WP_ID"
    BASE_SHA="$(git -C "$WT" rev-parse HEAD)"
    echo "seeded and committed baseline files from $SEED_DIR (new gate base: $BASE_SHA)"
  fi
fi

LOG_DIR="$REPO/agentwork/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
ATTEMPT_UUID="${ATTEMPT_UUID:-$(uuidgen 2>/dev/null || echo "$STAMP-$$-$RANDOM")}"
PROMPT_FILE="$LOG_DIR/${WP_ID}-${AGENT}-${STAMP}-${ATTEMPT_UUID:0:6}-prompt.md"
LOG_FILE="$LOG_DIR/${WP_ID}-${AGENT}-${STAMP}-${ATTEMPT_UUID:0:6}.log"
emit_prompt > "$PROMPT_FILE"

# Prepare isolated per-attempt directories
ATTEMPT_DIR="/tmp/agentwork-attempts/${ATTEMPT_UUID}"
mkdir -p "$ATTEMPT_DIR"/{codex,tmp,uv-cache}
chmod 700 "$ATTEMPT_DIR"
trap 'rm -rf "$ATTEMPT_DIR"' EXIT

if [ -f "$HOME/.codex/auth.json" ]; then
  cp "$HOME/.codex/auth.json" "$ATTEMPT_DIR/codex/" 2>/dev/null || true
elif [ -f "$HOME/.codex-cplt/auth.json" ]; then
  cp "$HOME/.codex-cplt/auth.json" "$ATTEMPT_DIR/codex/" 2>/dev/null || true
fi

export TMPDIR="$ATTEMPT_DIR/tmp"
export UV_CACHE_DIR="$ATTEMPT_DIR/uv-cache"
export CODEX_HOME="$ATTEMPT_DIR/codex"

if [ -x "$WT/agentwork/scripts/prepare-worktree.sh" ]; then
  ( cd "$WT" && agentwork/scripts/prepare-worktree.sh ) || echo "worktree prep failed (continuing)"
fi

PROMPT="$(cat "$PROMPT_FILE")"
cd "$WT"
echo "--- launching $AGENT on $WP_ID in $WT ---"

RUN_CMD=()
case "$AGENT" in
  fake)
    if [ -n "${FAKE_WORKER_SCRIPT:-}" ] && [ -f "$FAKE_WORKER_SCRIPT" ]; then
      RUN_CMD=(bash "$FAKE_WORKER_SCRIPT" "$WP_ID" "$WT" "$BRANCH")
    else
      RUN_CMD=(bash -c "echo 'fake worker pass' && git status")
    fi
    ;;

  claude)
    RUN_CMD=(env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
      claude -p --permission-mode bypassPermissions \
      --settings '{"env":{"ANTHROPIC_BASE_URL":null}}' "$PROMPT")
    ;;

  claude-or*)
    ALIAS="${AGENT#claude-or:}"
    if command -v claude-or >/dev/null 2>&1; then
      if [ "$ALIAS" != "$AGENT" ]; then
        RUN_CMD=(claude-or "$ALIAS" --permission-mode bypassPermissions "$PROMPT")
      else
        RUN_CMD=(claude-or --permission-mode bypassPermissions "$PROMPT")
      fi
    elif command -v claude >/dev/null 2>&1 && [ -n "${OPENROUTER_API_KEY:-}" ]; then
      OR_MODEL="${OPENROUTER_MODEL:-nvidia/llama-3.1-nemotron-ultra-253b-v1:free}"
      RUN_CMD=(env ANTHROPIC_BASE_URL="https://openrouter.ai/api" ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY" \
        claude -p --model "$OR_MODEL" --permission-mode bypassPermissions "$PROMPT")
    else
      echo "ERROR: 'claude-or' wrapper not found and OPENROUTER_API_KEY not set." >&2
      exit 1
    fi
    ;;

  claude-local)
    if command -v claude-local >/dev/null 2>&1; then
      RUN_CMD=(claude-local -p --permission-mode bypassPermissions "$PROMPT")
    elif command -v claude >/dev/null 2>&1; then
      LOCAL_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
      LOCAL_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:32b}"
      RUN_CMD=(env ANTHROPIC_BASE_URL="$LOCAL_URL" ANTHROPIC_API_KEY="ollama" \
        claude -p --model "$LOCAL_MODEL" --permission-mode bypassPermissions "$PROMPT")
    else
      echo "ERROR: 'claude-local' or 'claude' CLI not found." >&2
      exit 1
    fi
    ;;

  codex)
    RUN_CMD=(codex exec --dangerously-bypass-approvals-and-sandbox --ephemeral "$PROMPT")
    ;;

  agy)
    RUN_CMD=(agy --dangerously-skip-permissions -p --print-timeout "${PRINT_TIMEOUT:-180m}" "$PROMPT")
    ;;

  gemini)
    RUN_CMD=(gemini -y -p "$PROMPT")
    ;;

  pi-code|pi-devstral)
    RUN_CMD=(pi --provider "${PI_PROVIDER:-ollama}" --model "$AGENT" --thinking off \
      --tools "read,bash,edit,write,subagent,grep,find,ls" -p "$PROMPT")
    ;;

  pi-fw)
    RUN_CMD=(pi --provider fireworks --model "${PI_FW_MODEL:-kimi-k3}" \
      --tools "read,bash,edit,write,subagent,grep,find,ls" -p "$PROMPT")
    ;;

  *)
    echo "ERROR: Unknown agent '$AGENT'" >&2
    exit 1
    ;;
esac

# Execute under macOS Seatbelt, Linux Bubblewrap (bwrap), or worktree-only
RC=0
if [ "$ISOLATION_MODE" = "worktree-seatbelt" ] && [ "$AGENT" != "fake" ]; then
  if command -v sandbox-exec >/dev/null 2>&1; then
    REAL_WT="$(cd "$WT" && pwd -P)"
    REAL_GIT="$(cd "$COMMON_GIT" && pwd -P)"
    BRANCH_LOWER="$(printf '%s' "$WP_ID" | tr '[:upper:]' '[:lower:]')"

    SEATBELT_PROFILE="(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath \"$REAL_WT\")
  (subpath \"$REAL_GIT/worktrees/$WP_ID\")
  (subpath \"$REAL_GIT/objects\")
  (subpath \"$ATTEMPT_DIR\")
  (subpath \"/private/tmp\")
  (regex #\"^/private/tmp/\")
  (regex #\"^$REAL_GIT/(logs/)?refs/heads/wp/$BRANCH_LOWER(\\.lock)?$\"))
(deny file-read*
  (subpath \"$HOME/.ssh\")
  (subpath \"$HOME/.aws\")
  (subpath \"$HOME/.config/gh\")
  (subpath \"$HOME/.local/state/agentwork\"))"

    set +e
    sandbox-exec -p "$SEATBELT_PROFILE" "${RUN_CMD[@]}" 2>&1 | tee "$LOG_FILE"
    RC=${PIPESTATUS[0]:-$?}
    set -e
  elif command -v bwrap >/dev/null 2>&1; then
    REAL_WT="$(cd "$WT" && pwd -P)"
    REAL_GIT="$(cd "$COMMON_GIT" && pwd -P)"
    mkdir -p "$REAL_GIT/worktrees/$WP_ID" "$REAL_GIT/objects" "$REAL_GIT/refs/heads/wp" "$REAL_GIT/logs/refs/heads/wp"
    set +e
    bwrap --ro-bind / / \
      --dev /dev \
      --proc /proc \
      --bind "$REAL_WT" "$REAL_WT" \
      --bind "$REAL_GIT/worktrees/$WP_ID" "$REAL_GIT/worktrees/$WP_ID" \
      --bind "$REAL_GIT/objects" "$REAL_GIT/objects" \
      --bind "$REAL_GIT/refs/heads/wp" "$REAL_GIT/refs/heads/wp" \
      --bind "$REAL_GIT/logs/refs/heads/wp" "$REAL_GIT/logs/refs/heads/wp" \
      --bind "$ATTEMPT_DIR" "$ATTEMPT_DIR" \
      --bind /tmp /tmp \
      --tmpfs "$HOME/.ssh" \
      --tmpfs "$HOME/.aws" \
      --chdir "$REAL_WT" \
      "${RUN_CMD[@]}" 2>&1 | tee "$LOG_FILE"
    RC=${PIPESTATUS[0]:-$?}
    set -e
  else
    echo "ERROR: isolation_mode is 'worktree-seatbelt' but neither 'sandbox-exec' (macOS) nor 'bwrap' (Linux) is installed. Set isolation_mode: worktree-only in project.yaml to run without OS sandboxing." >&2
    exit 2
  fi
elif [ "$ISOLATION_MODE" = "worktree-only" ] || [ "$AGENT" = "fake" ]; then
  set +e
  "${RUN_CMD[@]}" 2>&1 | tee "$LOG_FILE"
  RC=${PIPESTATUS[0]:-$?}
  set -e
else
  echo "ERROR: Unsupported isolation_mode '$ISOLATION_MODE'. Supported: worktree-seatbelt | worktree-only" >&2
  exit 2
fi

echo "--- $AGENT exited with code $RC ---"

# Always run mechanical gate check via verify_gate.sh with explicit BASE_SHA
VERIFY_SCRIPT="$REPO/agentwork/scripts/verify_gate.sh"
echo "--- running mechanical gate for $WP_ID ---"
if ! bash "$VERIFY_SCRIPT" "$WP_ID" "$WT" "$BASE_SHA"; then
  echo "GATE: Mechanical gate FAILED."
  RC=1
else
  echo "GATE: Mechanical gate PASSED."
fi

exit "$RC"
