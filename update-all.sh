#!/usr/bin/env bash
# Update every project that uses this template.
#
# Usage:
#   update-all.sh [--dry-run] [--force] [--roots "dir1 dir2"]
#
set -euo pipefail

TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$TEMPLATE_DIR/setup.sh"
[ -x "$SETUP" ] || { echo "ERROR: $SETUP not found or not executable" >&2; exit 2; }

ROOTS=("$HOME/Code")
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --roots)
      IFS=':' read -r -a ROOTS <<< "${2// /:}"
      shift 2
      ;;
    --dry-run|--force) PASS+=("$1"); shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown option $1" >&2; exit 2 ;;
  esac
done

PROJECTS=()
for root in "${ROOTS[@]}"; do
  [ -d "$root" ] || continue
  while IFS= read -r config; do
    # /path/to/repo/agentwork/config/project.yaml -> 3 dirnames to reach /path/to/repo
    dir="$(dirname "$(dirname "$(dirname "$config")")")"
    [ -d "$dir/.ai" ] || continue
    [ "$dir" = "$TEMPLATE_DIR" ] && continue
    case "$dir" in *-agent-trees/*|*-trees/*|*/.git/*) continue ;; esac
    PROJECTS+=("$dir")
  done < <(find "$root" -maxdepth 5 -path '*/agentwork/config/project.yaml' 2>/dev/null | sort)
done

if [ "${#PROJECTS[@]}" -eq 0 ]; then
  echo "No template projects found under: ${ROOTS[*]}"
  exit 0
fi

echo "Found ${#PROJECTS[@]} project(s) to update."
for dir in "${PROJECTS[@]}"; do
  echo "=== Updating $dir ==="
  "$SETUP" "$dir" ${PASS+"${PASS[@]}"} || echo "WARNING: Failed updating $dir"
done
echo "Update complete."
