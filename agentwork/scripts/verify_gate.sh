#!/usr/bin/env bash
# Layer-1 Mechanical Verification Gate
# Usage:
#   verify_gate.sh <TASK_ID> [WORKTREE_DIR] [BASE_SHA]
#
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${HOME}/.local/bin:${PATH:-}"

[ $# -ge 1 ] || { echo "Usage: verify_gate.sh <TASK_ID> [WORKTREE_DIR] [BASE_SHA]" >&2; exit 1; }
TASK_ID="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
TARGET_DIR="${2:-.}"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

COMMON_GIT="$(git -C "$TARGET_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "")"
if [ -n "$COMMON_GIT" ]; then
  CANONICAL_REPO="$(cd "$COMMON_GIT/.." && pwd)"
else
  CANONICAL_REPO="$(git -C "$TARGET_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
REPO="$CANONICAL_REPO"
BASE_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || echo "main")"
BASE_SHA="${3:-$(git -C "$REPO" rev-parse "${BASE_BRANCH:-HEAD}" 2>/dev/null || echo "")}"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif [ -x "/usr/bin/python3" ]; then
    PYTHON_BIN="/usr/bin/python3"
  else
    PYTHON_BIN="python3"
  fi
fi

# Read test_command and lint_command from project.yaml
PROJECT_YAML="$REPO/agentwork/config/project.yaml"
TEST_CMD=""
LINT_CMD=""
if [ -f "$PROJECT_YAML" ]; then
  TEST_CMD="$(sed -n 's/^[[:space:]]*test_command:[[:space:]]*//p' "$PROJECT_YAML" | head -n1 | sed 's/^["'\'']//;s/["'\'']$//' || true)"
  LINT_CMD="$(sed -n 's/^[[:space:]]*lint_command:[[:space:]]*//p' "$PROJECT_YAML" | head -n1 | sed 's/^["'\'']//;s/["'\'']$//' || true)"
fi

# Special handling for FINAL repository-wide acceptance gate
if [ "$TASK_ID" = "FINAL" ]; then
  echo "=== Verifying FINAL Repository Acceptance Gate (target: $TARGET_DIR) ==="
  DIRTY="$(git -C "$TARGET_DIR" status --porcelain 2>/dev/null || true)"
  if [ -n "$DIRTY" ]; then
    echo "GATE FAIL: Working tree has uncommitted modifications:" >&2
    echo "$DIRTY" | sed 's/^/    /' >&2
    exit 3
  fi
  if [ -n "$LINT_CMD" ]; then
    echo "Running project lint_command: $LINT_CMD"
    ( cd "$TARGET_DIR" && export PYTHONPATH="${TARGET_DIR}/src:${TARGET_DIR}:${PYTHONPATH:-}" && eval "$LINT_CMD" ) || {
      echo "GATE FAIL: Project lint_command failed." >&2
      exit 9
    }
  fi
  if [ -n "$TEST_CMD" ]; then
    echo "Running project test_command: $TEST_CMD"
    ( cd "$TARGET_DIR" && export PYTHONPATH="${TARGET_DIR}/src:${TARGET_DIR}:${PYTHONPATH:-}" && eval "$TEST_CMD" ) || {
      echo "GATE FAIL: Project test_command failed." >&2
      exit 9
    }
  fi
  echo "=== GATE PASS: FINAL repository acceptance checks passed ==="
  exit 0
fi

echo "=== Verifying Gate for Task: $TASK_ID (target: $TARGET_DIR, base: ${BASE_SHA:-unknown}) ==="

# 1. Locate exact task specification in canonical repo or target worktree (excluding -SPLIT.md and templates)
find_task_spec() {
  local tid="$1"
  for d in "$REPO/agentwork/packages" "$REPO/agentwork/steps" "$TARGET_DIR/agentwork/packages"; do
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

TASK_FILE="$(find_task_spec "$TASK_ID" || true)"
[ -n "$TASK_FILE" ] || { echo "GATE FAIL: Spec for task '$TASK_ID' not found in $REPO or $TARGET_DIR." >&2; exit 2; }

# 2. Check uncommitted changes in target worktree
DIRTY="$(git -C "$TARGET_DIR" status --porcelain 2>/dev/null || true)"
if [ -n "$DIRTY" ]; then
  echo "GATE FAIL: Worktree has uncommitted modifications:" >&2
  echo "$DIRTY" | sed 's/^/    /' >&2
  exit 3
fi

# 3. Verify commits exist on this branch and resolve BASE_SHA safely
HEAD_SHA="$(git -C "$TARGET_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
if [ -z "$HEAD_SHA" ]; then
  echo "GATE FAIL: Could not determine HEAD commit." >&2
  exit 4
fi
if [ -z "$BASE_SHA" ] || ! git -C "$TARGET_DIR" rev-parse --verify "${BASE_SHA}^{commit}" >/dev/null 2>&1; then
  echo "GATE FAIL: Invalid or missing BASE_SHA '$BASE_SHA'." >&2
  exit 4
fi
if [ "$HEAD_SHA" = "$BASE_SHA" ]; then
  echo "GATE FAIL: Worker produced no new commits relative to base $BASE_SHA." >&2
  exit 4
fi

CHANGED_FILES="$(git -C "$TARGET_DIR" diff --name-only "$BASE_SHA" "$HEAD_SHA")" || {
  echo "GATE FAIL: git diff failed between $BASE_SHA and $HEAD_SHA." >&2
  exit 4
}
if [ -z "$CHANGED_FILES" ]; then
  echo "GATE FAIL: Diff between $BASE_SHA and $HEAD_SHA is empty." >&2
  exit 4
fi

# 4. Check protected files, ownership (owns:), locked_tests:, and report inclusion in diff
REPORT_NAME="$(printf '%s' "$TASK_ID" | tr '[:upper:]' '[:lower:]')"
"$PYTHON_BIN" - "$TASK_FILE" "$CHANGED_FILES" "$REPORT_NAME" "$TARGET_DIR" "$PROJECT_YAML" <<'EOF'
import sys, re, os

task_file = sys.argv[1]
changed = [f.strip() for f in sys.argv[2].strip().splitlines() if f.strip()]
report_name = sys.argv[3]
target_dir = sys.argv[4]
project_yaml = sys.argv[5]
content = open(task_file, "r", encoding="utf-8").read()

def glob_to_regex(pat: str) -> re.Pattern:
    """Convert path glob to regex where * does not cross / and ** crosses /."""
    pat = pat.strip().lstrip("./")
    if pat.endswith("/**"):
        base = re.escape(pat[:-3].rstrip("/"))
        return re.compile(rf"^{base}(/.*)?$")
    i, n = 0, len(pat)
    res = ["^"]
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                res.append(".*")
                i += 2
                continue
            else:
                res.append("[^/]*")
        elif c == "?":
            res.append("[^/]")
        else:
            res.append(re.escape(c))
        i += 1
    res.append("$")
    return re.compile("".join(res))

def matches_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if glob_to_regex(pat).match(path):
            return True
    return False

def extract_yaml_list(text: str, key: str) -> list[str]:
    items = []
    in_list = False
    for line in text.splitlines():
        line_s = line.strip()
        if re.match(rf"^{key}:\s*$", line_s):
            in_list = True
            continue
        m_inline = re.match(rf"^{key}:\s*\[(.*)\]$", line_s)
        if m_inline:
            for x in m_inline.group(1).split(","):
                x = x.strip().strip('"').strip("'")
                if x:
                    items.append(x)
            continue
        if in_list:
            if line_s.startswith("-"):
                items.append(line_s.lstrip("-").strip().strip('"').strip("'"))
            elif line and not line.startswith(" "):
                in_list = False
    return items

# 4a. Check protected files (built-in + project.yaml protected_files:)
protected_patterns = [
    "docs/design/DESIGN.md",
    "agentwork/config/**",
    "agentwork/state/DECISIONS.md",
    ".claude/**",
    ".codex/**",
    ".agents/**",
    "agentwork/pipeline.py",
    "agentwork/tui.py",
    "agentwork/scripts/**",
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
]
if os.path.isfile(project_yaml):
    py_text = open(project_yaml, "r", encoding="utf-8").read()
    protected_patterns.extend(extract_yaml_list(py_text, "protected_files"))

for f in changed:
    if matches_any(f, protected_patterns):
        print(f"GATE FAIL: Protected file was modified: {f}", file=sys.stderr)
        sys.exit(5)

# 5a. Extract owns and locked_tests
owns_patterns = extract_yaml_list(content, "owns")
if not owns_patterns:
    in_owns_md = False
    for line in content.splitlines():
        if re.match(r"^##\s+Owns", line):
            in_owns_md = True
            continue
        if in_owns_md:
            if line.startswith("## "):
                in_owns_md = False
            elif line.strip().startswith("-"):
                m = re.search(r"`([^`]+)`", line)
                if m:
                    owns_patterns.append(m.group(1))

locked_patterns = extract_yaml_list(content, "locked_tests")

for f in changed:
    if matches_any(f, locked_patterns):
        print(f"GATE FAIL: Worker modified locked acceptance test file '{f}' (protected by locked_tests).", file=sys.stderr)
        sys.exit(8)

# 5b. Enforce owns boundaries (allowing exact report file)
exact_report = f"agentwork/reports/{report_name}.md"
allowed_patterns = list(owns_patterns) + [exact_report]
violating = [f for f in changed if not matches_any(f, allowed_patterns)]
if violating:
    print("GATE FAIL: Files modified outside declared ownership:", file=sys.stderr)
    for vf in violating:
        print(f"    {vf}", file=sys.stderr)
    sys.exit(6)

# 5c. Require exact durable report to be modified in this commit range and non-empty
if exact_report not in [f.lower() for f in changed]:
    print(f"GATE FAIL: Durable report '{exact_report}' was not created/updated in this task's commits.", file=sys.stderr)
    sys.exit(7)

rp = os.path.join(target_dir, exact_report)
if not os.path.isfile(rp) or os.path.getsize(rp) == 0:
    print(f"GATE FAIL: Durable report '{exact_report}' is missing or empty.", file=sys.stderr)
    sys.exit(7)
EOF

# 6. Check Python test files for syntax errors, skip decorators/assignments, or empty stubs
"$PYTHON_BIN" - "$TARGET_DIR" "$CHANGED_FILES" <<'EOF'
import sys, ast, os, re

target_dir = sys.argv[1]
changed = [f.strip() for f in sys.argv[2].strip().splitlines() if f.strip()]
py_test_files = [
    os.path.join(target_dir, f) for f in changed
    if f.endswith(".py") and (
        f.startswith("tests/") or
        re.match(r"^(test_.*|.*_test)\.py$", os.path.basename(f).lower())
    )
]

for tf in py_test_files:
    if not os.path.isfile(tf):
        continue
    try:
        source = open(tf, "r", encoding="utf-8").read()
        tree = ast.parse(source, filename=tf)
    except Exception as e:
        print(f"GATE FAIL: Failed to parse Python test file '{tf}': {e}", file=sys.stderr)
        sys.exit(8)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            if func_str in ("pytest.skip", "unittest.skip"):
                print(f"GATE FAIL: Test file '{tf}' calls {func_str}().", file=sys.stderr)
                sys.exit(8)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "pytestmark":
                    val_str = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
                    if "skip" in val_str or "xfail" in val_str:
                        print(f"GATE FAIL: Test file '{tf}' sets pytestmark skip/xfail ({val_str}).", file=sys.stderr)
                        sys.exit(8)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            stmts = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if not stmts or (len(stmts) == 1 and isinstance(stmts[0], ast.Pass)):
                print(f"GATE FAIL: Test function '{node.name}' in {tf} is an empty stub.", file=sys.stderr)
                sys.exit(8)
            for dec in node.decorator_list:
                dec_target = dec.func if isinstance(dec, ast.Call) else dec
                dec_str = ast.unparse(dec_target) if hasattr(ast, "unparse") else ""
                if re.search(r"\b(skip|skipif|xfail)\b", dec_str):
                    print(f"GATE FAIL: Test function '{node.name}' in {tf} uses a skip/xfail decorator ({dec_str}).", file=sys.stderr)
                    sys.exit(8)
EOF

# 7. Execute Verify command and done_when commands
VERIFY_CMDS=()
VERIFY_LINE="$(sed -n 's/^Verify:[[:space:]]*//p' "$TASK_FILE" | head -n1 || true)"
[ -n "$VERIFY_LINE" ] && VERIFY_CMDS+=("$VERIFY_LINE")

while IFS= read -r dw; do
  if [ -n "$dw" ] && [ "$dw" != "$VERIFY_LINE" ]; then
    VERIFY_CMDS+=("$dw")
  fi
done < <("$PYTHON_BIN" - "$TASK_FILE" <<'EOF'
import sys, re
content = open(sys.argv[1], "r", encoding="utf-8").read()
in_dw = False
for line in content.splitlines():
    s = line.strip()
    if re.match(r"^done_when:\s*$", s):
        in_dw = True
        continue
    if in_dw:
        if s.startswith("-"):
            print(s.lstrip("-").strip().strip('"').strip("'"))
        elif line and not line.startswith(" "):
            in_dw = False
EOF
)

if [ "${#VERIFY_CMDS[@]}" -eq 0 ]; then
  echo "GATE FAIL: Task '$TASK_ID' has no 'Verify:' line or 'done_when:' commands." >&2
  exit 9
fi

for cmd in "${VERIFY_CMDS[@]}"; do
  echo "Running verification command: $cmd"
  ( cd "$TARGET_DIR" && \
    export PYTHONPATH="${TARGET_DIR}/src:${TARGET_DIR}:${PYTHONPATH:-}" && \
    eval "$cmd" ) || {
      echo "GATE FAIL: Verification command failed: $cmd" >&2
      exit 9
    }
done

echo "=== GATE PASS: Task $TASK_ID passed all mechanical checks ==="
exit 0
