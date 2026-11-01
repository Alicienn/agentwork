#!/usr/bin/env bash
# Install or UPDATE the unified autonomous + exploration agent template in a project.
#
# Usage:
#   setup.sh [TARGET] [--profile <openrouter-first|sensitive|balanced|local-only>]
#                     [--isolation <worktree-seatbelt|worktree-only>]
#                     [--lock-push] [--dry-run] [--force]
#
# Safe to re-run:
#   MANAGED  harness logic owned by the template — updated in place if unmodified.
#   SEEDED   project-specific content — created once, then never touched.
set -euo pipefail

TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="."
DRY=0
FORCE=0
LOCK_PUSH=0
PROFILE=""
ISOLATION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)
      [ $# -ge 2 ] || { echo "ERROR: --profile requires a value" >&2; exit 2; }
      PROFILE="$2"; shift 2 ;;
    --isolation)
      [ $# -ge 2 ] || { echo "ERROR: --isolation requires a value" >&2; exit 2; }
      ISOLATION="$2"; shift 2 ;;
    --lock-push) LOCK_PUSH=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    -*)
      echo "ERROR: unknown option $1" >&2
      exit 2
      ;;
    *)
      TARGET="$1"
      shift
      ;;
  esac
done

if [ ! -d "$TARGET" ]; then
  if [ "$DRY" = "1" ]; then
    echo "would create target directory: $TARGET"
  else
    mkdir -p "$TARGET"
  fi
fi
[ -d "$TARGET" ] && TARGET="$(cd "$TARGET" && pwd)"

# Verify profile and isolation are provided or already configured in an existing target
TARGET_CONFIG="$TARGET/agentwork/config/project.yaml"
PROFILE_EXPLICIT=0
ISOLATION_EXPLICIT=0
[ -n "$PROFILE" ] && PROFILE_EXPLICIT=1
[ -n "$ISOLATION" ] && ISOLATION_EXPLICIT=1

if [ -z "$PROFILE" ] && [ -f "$TARGET_CONFIG" ]; then
  EXISTING_PROFILE="$(grep -E '^[[:space:]]*routing_profile:' "$TARGET_CONFIG" | head -n1 | awk '{print $2}' | tr -d '"'"'" || echo "")"
  if [ -n "$EXISTING_PROFILE" ] && [ "$EXISTING_PROFILE" != "<REQUIRED>" ]; then
    PROFILE="$EXISTING_PROFILE"
  fi
fi

if [ -z "$ISOLATION" ] && [ -f "$TARGET_CONFIG" ]; then
  EXISTING_ISOLATION="$(grep -E '^[[:space:]]*isolation_mode:' "$TARGET_CONFIG" | head -n1 | awk '{print $2}' | tr -d '"'"'" || echo "")"
  if [ -n "$EXISTING_ISOLATION" ]; then
    ISOLATION="$EXISTING_ISOLATION"
  fi
fi
[ -z "$ISOLATION" ] && ISOLATION="worktree-seatbelt"

if [ -z "$PROFILE" ]; then
  echo "ERROR: --profile is required on initial setup." >&2
  echo "Choices: openrouter-first | sensitive | balanced | local-only" >&2
  exit 1
fi

case "$PROFILE" in
  openrouter-first|sensitive|balanced|local-only) ;;
  *) echo "ERROR: Invalid profile '$PROFILE'. Must be: openrouter-first | sensitive | balanced | local-only" >&2; exit 1 ;;
esac

case "$ISOLATION" in
  worktree-seatbelt|worktree-only) ;;
  *) echo "ERROR: Invalid --isolation '$ISOLATION'. Must be: worktree-seatbelt | worktree-only" >&2; exit 1 ;;
esac

MANAGED=(
  AGENTS.md
  CLAUDE.md
  GEMINI.md
  "Bootstrap an Autonomous Multi-Agent Repository.md"
  .claude/settings.json
  .codex/config.toml
  .agents/rules/autonomous-driver.md
  .ai/index.md
  .ai/git.md
  .ai/workflows/_contract.md
  .ai/workflows/big-work.md
  .ai/workflows/plan-then-execute.md
  .ai/workflows/parallel-agents.md
  .ai/workflows/design-exploration.md
  .ai/workflows/design-example.md
  agentwork/README.md
  agentwork/pipeline.py
  agentwork/tui.py
  agentwork/scripts/launch.sh
  agentwork/scripts/verify_gate.sh
  agentwork/scripts/driver.sh
  agentwork/scripts/split.sh
  agentwork/packages/WP-template.md
)

SEEDED=(
  .ai/session.md
  .ai/commands.md
  .ai/decisions.md
  agentwork/CONTEXT.md
  agentwork/CONTEXT-lite.md
  agentwork/config/project.yaml
  agentwork/config/agents.yaml
  agentwork/state/PROJECT.md
  agentwork/state/DECISIONS.md
  agentwork/state/QUESTIONS.md
)

n_create=0 n_update=0 n_skip=0 n_custom=0
CUSTOM_LIST=""

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}
hash_stdin() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  else
    python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  fi
}

known_hashes() {
  local rel="$1"
  local src_override="${2:-$TEMPLATE_DIR/$rel}"
  [ -f "$src_override" ] && hash_file "$src_override"
  [ -f "$TEMPLATE_DIR/$rel" ] && hash_file "$TEMPLATE_DIR/$rel"
  if [ "$rel" = ".claude/settings.json" ] && [ -f "$TEMPLATE_DIR/.claude/settings.sensitive.json" ]; then
    hash_file "$TEMPLATE_DIR/.claude/settings.sensitive.json"
  fi
  git -C "$TEMPLATE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  local c
  for c in $(git -C "$TEMPLATE_DIR" log --all --format=%H -- "$rel" 2>/dev/null); do
    git -C "$TEMPLATE_DIR" show "$c:$rel" 2>/dev/null | hash_stdin
  done
}

say() { [ "$DRY" = "1" ] && echo "would $1" || echo "$1"; }

install_managed() {
  local rel="$1"
  local src="$TEMPLATE_DIR/$rel"
  if [ "$rel" = ".claude/settings.json" ] && [ "$PROFILE" = "sensitive" ] && [ -f "$TEMPLATE_DIR/.claude/settings.sensitive.json" ]; then
    src="$TEMPLATE_DIR/.claude/settings.sensitive.json"
  fi
  local dest="$TARGET/$rel"
  [ -f "$src" ] || return 0

  if [ ! -e "$dest" ]; then
    say "create: $rel"
    if [ "$DRY" = "0" ]; then mkdir -p "$(dirname "$dest")"; cp "$src" "$dest"; fi
    n_create=$((n_create+1)); return 0
  fi

  local local_hash template_hash
  local_hash="$(hash_file "$dest")"
  template_hash="$(hash_file "$src")"
  if [ "$local_hash" = "$template_hash" ]; then
    n_skip=$((n_skip+1)); return 0
  fi

  local known
  known="$(known_hashes "$rel" "$src")"
  if [[ $'\n'"$known"$'\n' == *$'\n'"$local_hash"$'\n'* ]]; then
    say "update: $rel"
    if [ "$DRY" = "0" ]; then cp "$src" "$dest"; fi
    n_update=$((n_update+1))
  elif [ "$FORCE" = "1" ]; then
    say "FORCE update: $rel (backup -> $rel.bak)"
    if [ "$DRY" = "0" ]; then cp "$dest" "$dest.bak"; cp "$src" "$dest"; fi
    n_update=$((n_update+1))
  else
    echo "KEEP:   $rel (locally modified — use --force to overwrite)"
    CUSTOM_LIST="$CUSTOM_LIST $rel"
    n_custom=$((n_custom+1))
  fi
}

install_seeded() {
  local rel="$1" src="$TEMPLATE_DIR/$1" dest="$TARGET/$1"
  [ -f "$src" ] || return 0
  if [ -e "$dest" ]; then n_skip=$((n_skip+1)); return 0; fi
  say "create: $rel"
  if [ "$DRY" = "0" ]; then mkdir -p "$(dirname "$dest")"; cp "$src" "$dest"; fi
  n_create=$((n_create+1))
}

echo "target: $TARGET (Profile: $PROFILE, Isolation: $ISOLATION)"
[ "$DRY" = "1" ] && echo "(dry run — nothing will be written)"

if [ "$DRY" = "0" ]; then
  mkdir -p "$TARGET/.ai"/{notes,scratch,plans,drafts,workflows}
  touch "$TARGET/.ai"/{notes,scratch,plans,drafts}/.gitkeep
  if [ ! -e "$TARGET/ai" ] || [ -L "$TARGET/ai" ]; then
    ln -snf .ai "$TARGET/ai"
  fi
  mkdir -p "$TARGET/agentwork"/{config,scripts,packages,reports,state,logs}
  touch "$TARGET/agentwork/reports/.gitkeep"
  mkdir -p "$TARGET/.claude" "$TARGET/.codex" "$TARGET/.agents/rules"
fi

NEW_CONFIG=0
[ ! -f "$TARGET_CONFIG" ] && NEW_CONFIG=1

for f in "${MANAGED[@]}"; do install_managed "$f"; done
for f in "${SEEDED[@]}";  do install_seeded  "$f"; done

# Configure project.yaml only when newly seeded or when --profile / --isolation were explicitly passed
if [ "$DRY" = "0" ] && [ -f "$TARGET_CONFIG" ]; then
  EXISTING_UUID="$(grep -E '^[[:space:]]*repo_id:' "$TARGET_CONFIG" | head -n1 | awk '{print $2}' | tr -d '"'"'" || echo "")"
  if [ -z "$EXISTING_UUID" ]; then
    REPO_UUID="$(uuidgen 2>/dev/null || date +%s)"
    sed -i '' "s/repo_id:.*/repo_id: \"$REPO_UUID\"/" "$TARGET_CONFIG" 2>/dev/null || \
    sed -i "s/repo_id:.*/repo_id: \"$REPO_UUID\"/" "$TARGET_CONFIG"
  fi
  if [ "$NEW_CONFIG" = "1" ] || [ "$PROFILE_EXPLICIT" = "1" ]; then
    sed -i '' "s/routing_profile:.*/routing_profile: \"$PROFILE\"/" "$TARGET_CONFIG" 2>/dev/null || \
    sed -i "s/routing_profile:.*/routing_profile: \"$PROFILE\"/" "$TARGET_CONFIG"
  fi
  if [ "$NEW_CONFIG" = "1" ] || [ "$ISOLATION_EXPLICIT" = "1" ]; then
    sed -i '' "s/isolation_mode:.*/isolation_mode: \"$ISOLATION\"/" "$TARGET_CONFIG" 2>/dev/null || \
    sed -i "s/isolation_mode:.*/isolation_mode: \"$ISOLATION\"/" "$TARGET_CONFIG"
  fi
fi

# Make scripts executable
if [ "$DRY" = "0" ]; then
  chmod +x "$TARGET"/agentwork/scripts/*.sh "$TARGET"/agentwork/pipeline.py "$TARGET"/agentwork/tui.py 2>/dev/null || true
fi

# Merge gitignore fragment
FRAGMENT="$TEMPLATE_DIR/.gitignore.fragment"
if [ -f "$FRAGMENT" ] && [ -d "$TARGET" ]; then
  if [ -f "$TARGET/.gitignore" ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      case "$line" in \#*) continue ;; esac
      if ! grep -qxF "$line" "$TARGET/.gitignore"; then
        say "gitignore: +$line"
        [ "$DRY" = "0" ] && printf "\n%s\n" "$line" >> "$TARGET/.gitignore"
      fi
    done < "$FRAGMENT"
  else
    say "create: .gitignore"
    [ "$DRY" = "0" ] && cp "$FRAGMENT" "$TARGET/.gitignore"
  fi
fi

# Initialize git if needed
if [ "$DRY" = "0" ] && command -v git >/dev/null 2>&1; then
  if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [ "${GIT_INIT:-1}" = "0" ]; then
      echo "skip:   git init (GIT_INIT=0)"
    else
      git -C "$TARGET" init -q && echo "init:   git repository"
    fi
  fi
  if [ "$LOCK_PUSH" = "1" ]; then
    git -C "$TARGET" config remote.origin.pushurl no_push 2>/dev/null || true
  fi
fi

echo "result: ${n_create} created, ${n_update} updated, ${n_skip} already current, ${n_custom} kept (local edits)"
[ "$n_custom" -gt 0 ] && echo "        locally modified:${CUSTOM_LIST}"
exit 0
