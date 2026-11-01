#!/usr/bin/env bash
# Split one oversized work package into smaller, lower-tier ones. Delegates the
# decomposition to a capable agent — it is a judgement call, not a text
# operation, so no script can do it directly.
#
# Companion to launch.sh (runs packages). See .ai/workflows/parallel-agents.md.
#
# Usage:
#   agentwork/scripts/split.sh <WP-ID> [claude|gemini|codex]
#                              [--target pi-code|hosted] [--tier <tier>] [--dry-run]
# Example:
#   agentwork/scripts/split.sh WP09 claude --target pi-code
#
# --target says WHO WILL IMPLEMENT the resulting packages, which changes how
# they must be written:
#
#   pi-code  (default)  a local 30B. Packages must be CLOSED: signatures fixed,
#                       a stub and the tests shipped in <pkg>.files/, nothing
#                       left to design, a Verify: line the harness can run.
#   hosted              claude/codex/gemini. Packages may leave bounded engineering
#                       choices FREE and be larger; no stub is needed.
#
# Establish the target before running this; reuse an explicit session choice,
# otherwise ask. The choice changes the output substantially.
#
# Writes NEW packages (WP<NN>a, WP<NN>b, …) and leaves the original untouched,
# so you can still run it on a hosted agent. Never runs a local model: splitting
# well needs more judgement than the packages it produces.
#
# Env overrides: same as launch.sh (CLAUDE_MODEL/GEMINI_MODEL/CODEX_MODEL,
#                *_FLAGS, AGENT_TREES).
set -euo pipefail

usage() { sed -n '2,20p' "$0"; exit 1; }
[ $# -ge 1 ] || usage
WP_ID="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"; shift
AGENT="claude"; TIER=""; TARGET="pi-code"; DRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    claude|gemini|codex) AGENT="$1"; shift ;;
    claude-local|pi-mini|pi-code|pi-local)
      echo "ERROR: splitting needs a capable agent; not $1" >&2; exit 1 ;;
    --target) TARGET="$2"; shift 2 ;;
    --tier) TIER="$2"; shift 2 ;;
    --dry-run) DRY="--dry-run"; shift ;;
    *) echo "ERROR: unknown option $1" >&2; exit 1 ;;
  esac
done
case "$TARGET" in
  pi-code|hosted) ;;
  *) echo "ERROR: --target must be pi-code or hosted" >&2; exit 1 ;;
esac
# Both targets produce implementation packages, so both default to `code`.
# --tier is there for the rare case you want something else (e.g. mini-code).
[ -n "$TIER" ] || TIER="code"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
BASE="$(git -C "$REPO" branch --show-current 2>/dev/null || echo "main")"

PROJECT_YAML="$REPO/agentwork/config/project.yaml"
ROUTING_PROFILE="openrouter-first"
if [ -f "$PROJECT_YAML" ]; then
  RP="$(grep -E '^[[:space:]]*routing_profile:' "$PROJECT_YAML" | head -n1 | awk '{print $2}' | tr -d '"'"'" || true)"
  [ -n "$RP" ] && ROUTING_PROFILE="$RP"
fi
if [ "$ROUTING_PROFILE" = "local-only" ]; then
  echo "SECURITY ERROR: split.sh requires a hosted reasoning agent ($AGENT), which is blocked under routing_profile 'local-only'." >&2
  exit 2
fi

PKG=""
for f in "$REPO/agentwork/packages"/*.md; do
  [ -f "$f" ] || continue
  b="$(basename "$f")"
  case "$b" in *-template.md|WP-template.md|*-SPLIT.md) continue ;; esac
  p="${b%%-*}"
  if [ "$(printf '%s' "$p" | tr '[:lower:]' '[:upper:]')" = "$WP_ID" ]; then
    PKG="$f"
    break
  fi
done
[ -n "$PKG" ] || { echo "ERROR: no agentwork/packages/${WP_ID}-*.md"; exit 1; }
PKG_NAME="$(basename "$PKG" .md)"

TREES="${AGENT_TREES:-$(dirname "$REPO")/$(basename "$REPO")-agent-trees}"
BRANCH="split/$(printf '%s' "$WP_ID" | tr '[:upper:]' '[:lower:]')"
WT="$TREES/SPLIT-$WP_ID"

emit_prompt() {
  cat <<EOF
You are decomposing one work package into several smaller ones for a
parallel-agents workflow. You are NOT writing implementation code.

Preserve the parent's accepted design revision, node IDs, constraints, and FREE
bounds in every child. Unspecified behavior is not permission to invent. Do not
resolve OPEN, PROVISIONAL, or DEFERRED semantics to make a package executable.
Report such blockers for human steering; emit executable children only for
independent authorized scope. Trace new observable test expectations to accepted
intent/design. Include the relevant contract excerpt even with older seeded briefs.

EOF
if [ "$TARGET" = "pi-code" ]; then
  cat <<EOF
# These packages will be implemented by a LOCAL 30B model

It has no internet, a modest context window, and it fails on open design
decisions rather than on volume. Splitting a big package into three equally open
ones achieves nothing. Each package you write must be *closed*: everything to
decide is already decided.

So, for every package you produce:

- Fix the interfaces — data shapes, function signatures, field names, return
  types. The implementer implements; it does not design.
- **Ship a stub and the tests as files, not as code blocks in the spec.** Create
  \`agentwork/packages/<pkg-name>.files/\` mirroring repo paths, containing:
    * the target source file as a stub — real signatures and docstrings, every
      body \`raise NotImplementedError\`;
    * the test file that checks accepted behavior (not new design authority).
  The harness copies these into the worktree before the run. The spec then says
  "these are already in your worktree; implement the bodies, do not edit the
  tests". **Never paste code into the spec inside a fenced block for the agent
  to write out** — small models copy the fence markers into the file verbatim
  and break it.
- Give each package one or two owned files, disjoint from every sibling.
- Leave nothing to invent: no "choose a suitable X", no "design a scheme for Y".
  Resolve only engineering choices already within delegated bounds and record
  them. If closure needs a semantic decision, report a blocker instead.
- Add a \`Verify:\` line near the top: one shell command, run by the harness
  after the agent exits, that exits non-zero on failure. Its result decides
  whether the package is done.
EOF
else
  cat <<EOF
# These packages will be implemented by a HOSTED agent (claude/codex/gemini)

Packages may be larger and leave engineering choices FREE within explicit bounds.
State accepted semantics, constraints, and invariants; do not leave required
product semantics OPEN. Plan-tier exploration may propose unaccepted alternatives,
but those proposals do not authorize implementation.

- No stub files are needed; do not create a \`.files/\` directory.
- Describe the deliverables and the acceptance criteria rather than fixing every
  signature.
- Still give each package a disjoint set of owned files — that is what makes
  parallel runs safe.
- Add a \`Verify:\` line near the top where a mechanical check exists: one shell
  command, run by the harness after the agent exits.
EOF
fi
  cat <<EOF

Order the packages so that any shared type or interface is created by the FIRST
package, and later ones consume it. Use a soft dependency only with an already
authorized fallback; otherwise mark it blocking and order execution accordingly.

# The tier line

Every package you write starts with \`Tier: $TIER\` on its own line, directly
under the title.

# Your task

Split **$PKG_NAME** into the smallest number of \`Tier: $TIER\` packages that
meet the target's constraints above — prefer 2–4. Name them ${WP_ID}a-<slug>.md,
${WP_ID}b-<slug>.md, … in \`agentwork/packages/\`.

Do NOT modify or delete $PKG_NAME — it stays, so it can still be run whole on a
capable agent.

Also write \`agentwork/packages/${WP_ID}-SPLIT.md\`: which package does what,
the order to run them in, interfaces fixed by accepted authority, delegated
engineering choices, and unresolved blockers requiring human steering.

Follow the shape of \`agentwork/packages/WP-template.md\`. Commit locally.
Never push. Do not touch source or test files outside the package specs.

---

# The package being split

EOF
  cat "$PKG"
  printf '\n\n---\n\n# The shared brief these packages are written against\n\n'
  cat "$REPO/agentwork/CONTEXT.md"
  # Only worth showing when the packages you write will actually receive it.
  if [ "$TIER" = "mini-code" ] && [ -f "$REPO/agentwork/CONTEXT-lite.md" ]; then
    printf '\n\n---\n\n# The lite brief a Tier: mini-code package will actually receive\n\n'
    printf 'Anything such a package needs that is NOT in here must be stated in the\n'
    printf 'package spec itself, because the agent will never see the full brief.\n\n'
    cat "$REPO/agentwork/CONTEXT-lite.md"
  fi
  printf '\n\n---\nExecution: you are in worktree %s on branch %s. Work only here.\n' "$WT" "$BRANCH"
}

if [ "$DRY" = "--dry-run" ]; then
  echo "package: $PKG_NAME | agent: $AGENT | target: $TARGET | tier: $TIER | (dry run — nothing created)"
  echo "--- assembled prompt (preview) ---"
  emit_prompt | awk 'NR<=45'
  exit 0
fi

mkdir -p "$TREES"
if [ ! -d "$WT" ]; then
  if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$REPO" worktree add "$WT" "$BRANCH"
  else
    git -C "$REPO" worktree add -b "$BRANCH" "$WT" "$BASE"
  fi
  echo "worktree: $WT on $BRANCH (base $BASE)"
else
  echo "worktree: reusing $WT"
fi

LOG_DIR="$REPO/agentwork/logs"; mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
PROMPT_FILE="$LOG_DIR/SPLIT-$WP_ID-$AGENT-$STAMP-prompt.md"
LOG_FILE="$LOG_DIR/SPLIT-$WP_ID-$AGENT-$STAMP.log"
emit_prompt > "$PROMPT_FILE"
echo "package: $PKG_NAME | agent: $AGENT | target: $TARGET | tier: $TIER | prompt: $PROMPT_FILE"

cd "$WT"
echo "--- splitting $WP_ID with $AGENT in $WT ---"
set +e
case "$AGENT" in
  claude)
    env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
      claude -p ${CLAUDE_MODEL:+--model "$CLAUDE_MODEL"} ${CLAUDE_FLAGS:---permission-mode bypassPermissions} "$(cat "$PROMPT_FILE")" 2>&1 | tee "$LOG_FILE" ;;
  gemini)
    agy ${GEMINI_MODEL:+--model "$GEMINI_MODEL"} ${GEMINI_FLAGS:---dangerously-skip-permissions} --print-timeout "${GEMINI_PRINT_TIMEOUT:-180m}" -p "$(cat "$PROMPT_FILE")" 2>&1 | tee "$LOG_FILE" ;;
  codex)
    codex exec ${CODEX_MODEL:+-m "$CODEX_MODEL"} -c "sandbox_workspace_write.writable_roots=[\"$REPO/.git\"]" ${CODEX_FLAGS:---full-auto} "$(cat "$PROMPT_FILE")" 2>&1 | tee "$LOG_FILE" ;;
esac
RC=${PIPESTATUS[0]:-$?}; set -e
echo "--- $AGENT exited $RC ---"
git -C "$WT" status --short | head -20
echo "--- packages produced ---"
PRODUCED="$(find "$WT/agentwork/packages" -maxdepth 1 \( -iname "${WP_ID}[a-z]-*.md" -o -iname "${WP_ID}-SPLIT.md" \) 2>/dev/null | sed 's|.*/|  |' || true)"
if [ -n "$PRODUCED" ]; then
  echo "$PRODUCED"
else
  echo "  none — re-run to resume"
fi
echo
echo "Review the specs, then merge the branch and launch them:"
echo "  git merge --no-ff $BRANCH"
echo "  agentwork/scripts/launch.sh ${WP_ID}a claude-local"
exit "$RC"
