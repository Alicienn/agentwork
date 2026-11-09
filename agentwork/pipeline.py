#!/usr/bin/env python3
"""
Autonomous Multi-Agent Engineering Pipeline Driver
Deterministic orchestration, task lifecycle, dependency resolution, sealing, and integration.
"""

import os
import sys
import ast
import json
import re
import time
import glob
import signal
import shutil
import hashlib
import argparse
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
from awlib import config as awconfig  # noqa: E402
from awlib import oscompat  # noqa: E402
from awlib import rustsrc  # noqa: E402
from awlib.oscompat import FileLock, LockBusy, stable_file_bytes  # noqa: E402
from awlib.specs import parse_frontmatter  # noqa: E402,F401  (re-exported for compatibility)

# Ignore SIGPIPE so closing TUI or piping output never crashes the controller mid-commit
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

REPO_ROOT = Path(__file__).resolve().parent.parent
STEPS_DIR = REPO_ROOT / "agentwork" / "steps"
PACKAGES_DIR = REPO_ROOT / "agentwork" / "packages"
CONFIG_DIR = REPO_ROOT / "agentwork" / "config"
SCRIPTS_DIR = REPO_ROOT / "agentwork" / "scripts"
PROJECT_CONFIG = CONFIG_DIR / "project.yaml"
AGENTS_CONFIG = CONFIG_DIR / "agents.yaml"
MANIFEST_FILE = CONFIG_DIR / "harness-manifest.json"
AWLIB_DIR = REPO_ROOT / "agentwork" / "awlib"
TOOLS_DIR = REPO_ROOT / "agentwork" / "tools"
PIPELINE = Path(__file__).resolve()
DESIGN_FILE = REPO_ROOT / "docs" / "design" / "DESIGN.md"


def get_trees_dir() -> Path:
    """Resolve worktrees root directory honoring AGENT_TREES."""
    env_trees = os.environ.get("AGENT_TREES")
    if env_trees:
        return Path(env_trees).resolve()
    return REPO_ROOT.parent / f"{REPO_ROOT.name}-agent-trees"


def get_state_dir(create: bool = True) -> Path:
    """Resolve state directory outside repo scoped by both repo_id and realpath hash."""
    repo_id = "default"
    if PROJECT_CONFIG.exists():
        content = PROJECT_CONFIG.read_text(encoding="utf-8")
        m = re.search(r"repo_id:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?", content)
        if m and m.group(1) and m.group(1) != "<REQUIRED>":
            repo_id = m.group(1)
    path_hash = hashlib.sha256(str(REPO_ROOT.resolve()).encode()).hexdigest()[:8]
    scoped_id = f"{repo_id}-{path_hash}"

    external_dir = oscompat.user_state_root() / scoped_id
    if not create:
        return external_dir
    try:
        external_dir.mkdir(parents=True, exist_ok=True)
        (external_dir / "attempts").mkdir(parents=True, exist_ok=True)
        return external_dir
    except (PermissionError, OSError):
        local_dir = REPO_ROOT / ".agentwork-state"
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "attempts").mkdir(parents=True, exist_ok=True)
        return local_dir


def state_paths(create: bool = True) -> Tuple[Path, Path, Path, Path, Path]:
    sdir = get_state_dir(create=create)
    return (
        sdir,
        sdir / "STATE.json",
        sdir / "STATE.lock",
        sdir / "DRIVE.lock",
        sdir / "journal.jsonl",
    )


def parse_project_config() -> Dict[str, Any]:
    """Read budget, timeout, and profile settings from project.yaml (flat legacy view)."""
    return awconfig.flat_view(full_project_config())


def full_project_config() -> Dict[str, Any]:
    try:
        return awconfig.load_project_config(PROJECT_CONFIG)
    except ValueError as e:
        print(f"CONFIG ERROR: cannot parse {PROJECT_CONFIG}: {e}", file=sys.stderr)
        sys.exit(15)


def get_profile() -> str:
    return str(parse_project_config()["routing_profile"])


def load_tasks_with_duplicates() -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Scan packages/ and steps/ to load task definitions and report any duplicate IDs."""
    tasks: Dict[str, Dict[str, Any]] = {}
    duplicates: List[str] = []
    for d in (PACKAGES_DIR, STEPS_DIR):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name.endswith("-template.md") or p.name.startswith("WP-template") or p.name.endswith("-SPLIT.md"):
                continue
            content = p.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(content)
            task_id = meta.get("id")
            if not task_id:
                task_id = p.stem.split("-")[0].upper()
            else:
                task_id = str(task_id).upper()
            meta["id"] = task_id
            rel_file = p.relative_to(REPO_ROOT).as_posix()
            if task_id in tasks:
                duplicates.append(f"Duplicate task ID '{task_id}' in {rel_file} and {tasks[task_id]['file']}")
            meta["file"] = rel_file
            meta["body"] = body
            meta["depends"] = [str(x).upper() for x in meta.get("depends", [])]
            meta["rung"] = int(meta.get("rung", 1))
            tasks[task_id] = meta
    return tasks, duplicates


def load_tasks() -> Dict[str, Dict[str, Any]]:
    tasks, _ = load_tasks_with_duplicates()
    return tasks


def load_state() -> Dict[str, Any]:
    """Load current state with file locking, failing loudly on corruption."""
    _, state_file, lock_file, _, _ = state_paths(create=True)
    with FileLock(lock_file, exclusive=False):
        try:
            if not state_file.exists():
                return {
                    "completed": [],
                    "running": [],
                    "blocked": [],
                    "questions": {},
                    "attempts": {},
                    "verified_heads": {},
                    "heartbeat_at": time.time(),
                }
            raw = state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data.get("blocked", []), list):
                data["blocked"] = list(data["blocked"])
            data.setdefault("questions", {})
            data.setdefault("verified_heads", {})
            return data
        except Exception as e:
            print(f"CRITICAL STATE ERROR: Failed to parse {state_file}: {e}", file=sys.stderr)
            sys.exit(13)


def save_state(state: Dict[str, Any]) -> None:
    """Atomically save authoritative state under exclusive flock and write read-only mirror."""
    sdir, state_file, lock_file, _, _ = state_paths(create=True)
    with FileLock(lock_file, exclusive=True):
        state["heartbeat_at"] = time.time()
        payload = json.dumps(state, indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=str(sdir), prefix="STATE.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            tf.write(payload)
            tf.flush()
            os.fsync(tf.fileno())
        for attempt in range(50):
            try:
                os.replace(tmp_path, state_file)
                break
            except PermissionError:
                # Windows: a reader (or antivirus) may briefly hold the file open.
                if attempt == 49:
                    raise
                time.sleep(0.1)

        mirror = REPO_ROOT / "agentwork" / "state" / "STATUS_MIRROR.json"
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_text(payload, encoding="utf-8")
        except Exception:
            pass


def journal_event(event_type: str, data: Dict[str, Any]) -> None:
    """Append-only audit journal."""
    _, _, _, _, journal_file = state_paths(create=True)
    entry = {
        "timestamp": time.time(),
        "event": event_type,
        "data": data,
    }
    with open(journal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def compute_task_statuses(tasks: Dict[str, Dict[str, Any]], state: Dict[str, Any]) -> Dict[str, str]:
    completed = set(state.get("completed", []))
    running = set(state.get("running", []))
    blocked = set(state.get("blocked", []))

    def has_blocked_ancestor(tid: str, seen: Optional[Set[str]] = None) -> bool:
        if seen is None:
            seen = set()
        if tid in seen:
            return False
        seen.add(tid)
        for d in tasks.get(tid, {}).get("depends", []):
            if d in blocked or has_blocked_ancestor(d, seen):
                return True
        return False

    statuses = {}
    for tid, t in tasks.items():
        if tid in completed:
            statuses[tid] = "DONE"
        elif tid in running:
            statuses[tid] = "RUNNING"
        elif tid in blocked:
            statuses[tid] = "BLOCKED_SELF"
        elif has_blocked_ancestor(tid):
            statuses[tid] = "BLOCKED_DEP"
        else:
            deps = t.get("depends", [])
            if all(d in completed for d in deps):
                statuses[tid] = "READY"
            else:
                statuses[tid] = "PENDING"
    return statuses


def _segment_matches(s1: str, s2: str) -> bool:
    """Check whether two single-directory glob segments can match a common filename."""
    # Convert single-segment glob (* and ?) to regex and test against synthetic candidates
    r1 = re.compile("^" + "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in s1) + "$")
    r2 = re.compile("^" + "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in s2) + "$")
    if s1 == s2 or s1 == "*" or s2 == "*":
        return True
    p1_lit = re.split(r"[\*\?]", s1)[0]
    p2_lit = re.split(r"[\*\?]", s2)[0]
    if p1_lit and p2_lit and not (p1_lit.startswith(p2_lit) or p2_lit.startswith(p1_lit)):
        return False
    suf1 = re.split(r"[\*\?]", s1)[-1]
    suf2 = re.split(r"[\*\?]", s2)[-1]
    if suf1 and suf2 and not (suf1.endswith(suf2) or suf2.endswith(suf1)):
        return False
    # Construct sample strings and test intersection
    prefix = p1_lit if len(p1_lit) >= len(p2_lit) else p2_lit
    suffix = suf1 if len(suf1) >= len(suf2) else suf2
    for mid in ("", "x", "test_x", "_"):
        sample = f"{prefix}{mid}{suffix}"
        if r1.match(sample) and r2.match(sample):
            return True
    return False


def globs_overlap(pat1: str, pat2: str) -> bool:
    """Sound segment-wise glob intersection check handling ** recursive wildcards and single-segment *."""
    segs1 = [s for s in pat1.strip().lstrip("./").rstrip("/").split("/") if s]
    segs2 = [s for s in pat2.strip().lstrip("./").rstrip("/").split("/") if s]

    memo: Dict[Tuple[int, int], bool] = {}

    def match(i: int, j: int) -> bool:
        if (i, j) in memo:
            return memo[(i, j)]
        if i == len(segs1) and j == len(segs2):
            return True
        if i < len(segs1) and segs1[i] == "**":
            # ** can match 0 segments or 1+ segments in segs2
            res = match(i + 1, j) or (j < len(segs2) and match(i, j + 1))
            memo[(i, j)] = res
            return res
        if j < len(segs2) and segs2[j] == "**":
            res = match(i, j + 1) or (i < len(segs1) and match(i + 1, j))
            memo[(i, j)] = res
            return res
        if i == len(segs1) or j == len(segs2):
            memo[(i, j)] = False
            return False
        if _segment_matches(segs1[i], segs2[j]):
            res = match(i + 1, j + 1)
            memo[(i, j)] = res
            return res
        memo[(i, j)] = False
        return False

    return match(0, 0)


def validate_tasks_internal() -> List[str]:
    """Return list of validation errors across the task DAG."""
    tasks, duplicates = load_tasks_with_duplicates()
    errors: List[str] = list(duplicates)

    for tid, t in tasks.items():
        for d in t.get("depends", []):
            if d not in tasks:
                errors.append(f"Task {tid} depends on unknown task {d}")

    visited: Dict[str, int] = {}

    def dfs(curr: str, path: List[str]):
        visited[curr] = 1
        for neighbor in tasks.get(curr, {}).get("depends", []):
            if neighbor in path:
                errors.append(f"Cycle detected: {' -> '.join(path + [neighbor])}")
            elif neighbor in tasks and visited.get(neighbor) != 2:
                dfs(neighbor, path + [neighbor])
        visited[curr] = 2

    for tid in tasks:
        if visited.get(tid) != 2:
            dfs(tid, [tid])

    def can_reach(src: str, dst: str, memo: Optional[Set[str]] = None) -> bool:
        if memo is None:
            memo = set()
        if src == dst:
            return True
        if src in memo:
            return False
        memo.add(src)
        for dep in tasks.get(src, {}).get("depends", []):
            if can_reach(dep, dst, memo):
                return True
        return False

    t_list = list(tasks.values())
    for i in range(len(t_list)):
        for j in range(i + 1, len(t_list)):
            t1, t2 = t_list[i], t_list[j]
            if not can_reach(t1["id"], t2["id"]) and not can_reach(t2["id"], t1["id"]):
                for o1 in t1.get("owns", []):
                    for o2 in t2.get("owns", []):
                        if globs_overlap(o1, o2):
                            errors.append(f"Overlap in concurrent owns between {t1['id']} ('{o1}') and {t2['id']} ('{o2}')")

    for tid, t in tasks.items():
        if t.get("kind", "implementation") == "implementation":
            has_verify = bool(re.search(r"^Verify:\s*\S+", t.get("body", ""), re.MULTILINE))
            has_done_when = bool(t.get("done_when"))
            if not has_verify and not has_done_when:
                errors.append(f"Task {tid} is missing a deterministic 'Verify:' line or 'done_when' list.")
        for lpat in t.get("locked_tests", []):
            for opat in t.get("owns", []):
                if globs_overlap(lpat, opat):
                    errors.append(f"Task {tid} has overlapping pattern between 'owns' ('{opat}') and 'locked_tests' ('{lpat}').")

    return errors


def _stable_hash(path: Path) -> str:
    return hashlib.sha256(stable_file_bytes(path)).hexdigest() if path.exists() else ""


def gate_source_files() -> List[Path]:
    """Every file whose content defines the gate's behaviour (sealed as gate_hash)."""
    files: List[Path] = [PIPELINE]
    if AWLIB_DIR.is_dir():
        files += sorted(AWLIB_DIR.glob("*.py"))
    tool = TOOLS_DIR / "rust-testcheck"
    for rel in ("Cargo.toml", "Cargo.lock", "src/main.rs"):
        if (tool / rel).exists():
            files.append(tool / rel)
    return files


def compute_gate_hash() -> str:
    h = hashlib.sha256()
    for f in gate_source_files():
        h.update(f.relative_to(REPO_ROOT).as_posix().encode())
        h.update(stable_file_bytes(f))
    return h.hexdigest()


def compute_current_manifest(tasks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute manifest hashes over design, config, scripts, specs, and .files/."""
    profile = get_profile()
    # Text files are hashed with CRLF normalized to LF so a seal made on Linux
    # verifies on a Windows checkout (core.autocrlf) and vice versa.
    design_hash = _stable_hash(DESIGN_FILE)
    policy_hash = _stable_hash(PROJECT_CONFIG)
    agents_hash = _stable_hash(AGENTS_CONFIG)
    gate_hash = compute_gate_hash()

    manifest: Dict[str, Any] = {
        "version": "2.0",
        "routing_profile": profile,
        "design_hash": design_hash,
        "policy_hash": policy_hash,
        "agents_hash": agents_hash,
        "gate_hash": gate_hash,
        "tasks": {},
    }

    memo_digests: Dict[str, str] = {}

    def get_task_digest(tid: str, stack: Optional[Set[str]] = None) -> str:
        if tid in memo_digests:
            return memo_digests[tid]
        if stack is None:
            stack = set()
        if tid in stack or tid not in tasks:
            return ""
        stack.add(tid)
        t = tasks[tid]
        spec_path = REPO_ROOT / t["file"]
        spec_hash = _stable_hash(spec_path)

        seed_dir = spec_path.with_suffix(".files")
        seed_hash_er = hashlib.sha256()
        if seed_dir.exists() and seed_dir.is_dir():
            for sf in sorted(seed_dir.rglob("*")):
                if sf.is_file():
                    seed_hash_er.update(sf.relative_to(seed_dir).as_posix().encode())
                    seed_hash_er.update(stable_file_bytes(sf))
        seed_hash = seed_hash_er.hexdigest()

        h = hashlib.sha256()
        h.update(policy_hash.encode())
        h.update(design_hash.encode())
        h.update(agents_hash.encode())
        h.update(gate_hash.encode())
        h.update(spec_hash.encode())
        h.update(seed_hash.encode())
        for dep in sorted(t.get("depends", [])):
            dep_digest = get_task_digest(dep, stack)
            h.update(f"{dep}:{dep_digest}".encode())

        digest = h.hexdigest()
        memo_digests[tid] = digest
        manifest["tasks"][tid] = {
            "file": t["file"],
            "spec_hash": spec_hash,
            "seed_hash": seed_hash,
            "digest": digest,
            "rung": t.get("rung", 1),
            "depends": t.get("depends", []),
        }
        return digest

    for tid in sorted(tasks.keys()):
        get_task_digest(tid)

    return manifest


def verify_seal_or_exit(tasks: Dict[str, Dict[str, Any]], require_manifest: bool = True) -> None:
    """Verify live repository inputs match the sealed harness-manifest.json; exit 14 on drift or missing seal."""
    if not MANIFEST_FILE.exists():
        if require_manifest:
            print(f"SEAL ERROR (exit 14): Manifest {MANIFEST_FILE.relative_to(REPO_ROOT)} not found. Run 'pipeline.py seal' before running or driving.", file=sys.stderr)
            sys.exit(14)
        return
    try:
        sealed = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"SEAL ERROR: Could not read {MANIFEST_FILE}: {e}", file=sys.stderr)
        sys.exit(14)

    current = compute_current_manifest(tasks)
    for key in ("routing_profile", "design_hash", "policy_hash", "agents_hash", "gate_hash"):
        if sealed.get(key) != current.get(key):
            print(f"SEALED INPUT CHANGED (exit 14): '{key}' drifted since seal. Run 'pipeline.py reseal' if intentional.", file=sys.stderr)
            sys.exit(14)

    sealed_tasks = sealed.get("tasks", {})
    curr_tasks = current.get("tasks", {})
    if set(sealed_tasks.keys()) != set(curr_tasks.keys()):
        print("SEALED INPUT CHANGED (exit 14): Task set changed since seal.", file=sys.stderr)
        sys.exit(14)

    for tid, st in sealed_tasks.items():
        ct = curr_tasks.get(tid, {})
        if st.get("digest") != ct.get("digest"):
            print(f"SEALED INPUT CHANGED (exit 14): Task '{tid}' or its upstream dependencies drifted since seal.", file=sys.stderr)
            sys.exit(14)


def run_process_with_group_timeout(cmd: List[str], cwd: Path, timeout_sec: int) -> int:
    """Run cmd and kill its whole process tree on timeout or Ctrl-C (POSIX + Windows)."""
    return oscompat.run_process_with_group_timeout(cmd, cwd=cwd, timeout_sec=timeout_sec)


def self_cmd(*args: str) -> List[str]:
    """argv that re-invokes this pipeline with the current interpreter."""
    return [sys.executable, str(PIPELINE), *args]


def cmd_doctor(args):
    """System and environment diagnostics."""
    print("=== Pipeline Doctor Diagnostics ===")
    cfg = parse_project_config()
    profile = cfg["routing_profile"]
    isolation = cfg["isolation_mode"]
    print(f"Project Profile: {profile} | Isolation: {isolation}")

    errors = []
    if profile == "<REQUIRED>" or profile not in ("openrouter-first", "sensitive", "balanced", "local-only"):
        errors.append(f"Invalid or unset routing_profile '{profile}' in project.yaml.")
    if isolation not in awconfig.VALID_ISOLATION:
        errors.append(f"Invalid isolation_mode '{isolation}' in project.yaml.")

    print(f"  ✓ python found: {sys.executable} ({sys.version.split()[0]})")
    if shutil.which("git"):
        print(f"  ✓ git found: {shutil.which('git')}")
    else:
        errors.append("Missing core binary: git")
    full = full_project_config()
    try:
        langs = awconfig.enabled_languages(full, REPO_ROOT)
    except ValueError as e:
        errors.append(str(e))
        langs = []
    print(f"  ✓ languages: {', '.join(langs) or 'none'}")
    if "rust" in langs:
        for b in ("cargo", "rustfmt"):
            if shutil.which(b):
                print(f"  ✓ {b} found: {shutil.which(b)}")
            else:
                errors.append(f"Rust enabled but '{b}' not found in PATH")
        if shutil.which("cargo"):
            clippy = subprocess.run(["cargo", "clippy", "--version"], capture_output=True, text=True)
            if clippy.returncode != 0:
                errors.append("Rust enabled but 'cargo clippy' is not installed (rustup component add clippy)")
            from awlib import langs as awlangs
            print(f"  ✓ rust test command: {awlangs.rust_test_command(full)}")
            if (full["rust"].get("mutants") or {}).get("enabled"):
                mut = subprocess.run(["cargo", "mutants", "--version"], capture_output=True, text=True)
                if mut.returncode != 0:
                    errors.append("rust.mutants.enabled is true but cargo-mutants is not installed (cargo install cargo-mutants)")
    shell_pref = str(full["verification"].get("shell") or "auto")
    try:
        print(f"  ✓ verification shell: {' '.join(oscompat.resolve_shell(shell_pref))}")
    except (FileNotFoundError, ValueError) as e:
        errors.append(str(e))

    # Check available agent CLIs
    installed_clis = [c for c in ("claude", "claude-or", "claude-local", "codex", "agy", "gemini", "pi-code") if is_agent_installed(c)]
    permitted_clis = [c for c in installed_clis if is_agent_allowed_by_profile(c, profile)]
    if permitted_clis:
        print(f"  ✓ Installed & profile-permitted agent CLIs: {', '.join(permitted_clis)}")
    else:
        print(f"  ! Warning: No standard agent CLIs detected in PATH for profile '{profile}' (AGENTWORK_DEFAULT_AGENT=fake still works for testing).")

    if profile in ("sensitive", "local-only"):
        print(f"  ✓ Profile '{profile}' active: verifying claude-or is rejected...")
        res = subprocess.run(self_cmd("launch", "DUMMY", "claude-or"), capture_output=True, text=True)
        if res.returncode == 2:
            print("  ✓ Local rejection test passed: claude-or blocked before execution.")
        else:
            errors.append("Profile guard failed: claude-or was not blocked with exit code 2.")

    res = subprocess.run(self_cmd("launch", "--selftest"), capture_output=True, text=True)
    if res.returncode == 0:
        print("  ✓ Sandbox selftest passed." + ("  (DEGRADED: no OS sandbox, worktree-only)" if "DEGRADED" in res.stdout else ""))
    else:
        errors.append(f"Sandbox selftest failed for isolation_mode '{isolation}'.")

    if errors:
        print("\nDoctor detected issues:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("\nAll diagnostics passed cleanly.")
        sys.exit(0)


def cmd_validate(args):
    """Validate task DAG and specifications."""
    tasks = load_tasks()
    print(f"Validating {len(tasks)} workpackages/steps...")
    errors = validate_tasks_internal()
    if errors:
        print("Validation FAILED:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("DAG validation PASSED cleanly.")
        sys.exit(0)


def cmd_seal(args):
    """Validate DAG, compute topological digests, and seal the manifest."""
    errors = validate_tasks_internal()
    if errors:
        print("ERROR: Cannot seal an invalid DAG:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)

    tasks = load_tasks()
    manifest = compute_current_manifest(tasks)
    manifest["sealed_at"] = time.time()
    generation = 1
    if MANIFEST_FILE.exists():
        try:
            old = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
            generation = int(old.get("generation", 1)) + 1
        except Exception:
            pass
    manifest["generation"] = generation

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    journal_event("seal", {"tasks_count": len(tasks), "generation": generation, "manifest": str(MANIFEST_FILE)})
    print(f"Sealed {len(tasks)} tasks (generation {generation}) to {MANIFEST_FILE.relative_to(REPO_ROOT)}")


def cmd_list(args):
    tasks = load_tasks()
    state = load_state()
    statuses = compute_task_statuses(tasks, state)

    if args.json:
        out = []
        for tid, t in sorted(tasks.items()):
            out.append({
                "id": tid,
                "name": t.get("name", tid),
                "rung": t.get("rung", 1),
                "status": statuses.get(tid, "UNKNOWN"),
                "depends": t.get("depends", []),
                "file": t.get("file"),
            })
        print(json.dumps(out, indent=2))
        return

    print(f"{'ID':<8} {'RUNG':<6} {'STATUS':<14} {'DEPENDS':<18} {'FILE'}")
    print("-" * 70)
    for tid, t in sorted(tasks.items()):
        status = statuses.get(tid, "UNKNOWN")
        deps = ",".join(t.get("depends", [])) or "—"
        rung = str(t.get("rung", 1))
        f = t.get("file", "")
        print(f"{tid:<8} {rung:<6} {status:<14} {deps:<18} {f}")


def cmd_next(args):
    tasks = load_tasks()
    state = load_state()
    statuses = compute_task_statuses(tasks, state)

    ready = [t for tid, t in sorted(tasks.items()) if statuses.get(tid) == "READY"]
    if not ready:
        if args.json:
            all_done = bool(tasks) and all(s == "DONE" for s in statuses.values())
            print(json.dumps({"action": "done" if all_done else "blocked", "id": None}))
        else:
            print("No tasks currently READY.")
        return

    nxt = ready[0]
    tid = nxt["id"]
    argv = self_cmd("run", tid)

    if args.json:
        print(json.dumps({
            "action": "run",
            "id": tid,
            "rung": nxt.get("rung", 1),
            "argv": argv,
            "file": nxt.get("file"),
            "depends": nxt.get("depends", []),
        }, indent=2))
    else:
        print(f"Next runnable task: {tid} (Rung {nxt.get('rung', 1)})")
        print(f"  Command: {' '.join(argv)}")


def extract_file_symbols(file_path: Path) -> Tuple[List[str], List[str]]:
    """Extract public signatures and imported modules from a source file."""
    signatures: List[str] = []
    imports: List[str] = []
    if not file_path.exists() or not file_path.is_file():
        return signatures, imports
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return signatures, imports

    if file_path.suffix == ".py":
        try:
            tree = ast.parse(text, filename=str(file_path))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for n in node.names:
                        imports.append(n.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        args_str = ast.unparse(node.args) if hasattr(ast, "unparse") else "..."
                        ret_str = f" -> {ast.unparse(node.returns)}" if getattr(node, "returns", None) and hasattr(ast, "unparse") else ""
                        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                        signatures.append(f"{prefix}{node.name}({args_str}){ret_str}")
                elif isinstance(node, ast.ClassDef):
                    if not node.name.startswith("_"):
                        bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                        base_str = f"({', '.join(bases)})" if bases else ""
                        methods = []
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not item.name.startswith("_") or item.name == "__init__"):
                                m_args = ast.unparse(item.args) if hasattr(ast, "unparse") else "..."
                                m_ret = f" -> {ast.unparse(item.returns)}" if getattr(item, "returns", None) and hasattr(ast, "unparse") else ""
                                methods.append(f"  def {item.name}({m_args}){m_ret}")
                        cls_sig = f"class {node.name}{base_str}:"
                        if methods:
                            cls_sig += "\n" + "\n".join(methods)
                        signatures.append(cls_sig)
        except Exception:
            pass
    elif file_path.suffix == ".rs":
        signatures = rustsrc.extract_signatures(text)
        mods, uses = rustsrc.extract_module_refs(text)
        imports = [f"rust-mod:{m}" for m in mods] + [f"rust-use:{u}" for u in uses]
    elif file_path.suffix in (".ts", ".tsx", ".js", ".go", ".c", ".h"):
        for line in text.splitlines():
            line_s = line.strip()
            if re.match(r"^(export\s+|pub\s+|func\s+|type\s+|interface\s+|struct\s+|class\s+|def\s+)", line_s):
                signatures.append(line_s.rstrip("{;").strip())
    return signatures, imports


def rust_module_candidates(src_file: Path, ref: str) -> List[Path]:
    """Resolve `mod foo;` (sibling file) or `use crate::a::b` (from the crate's src/) to files."""
    kind, _, name = ref.partition(":")
    out: List[Path] = []
    if kind == "rust-mod":
        base = src_file.parent if src_file.name in ("lib.rs", "main.rs", "mod.rs") else src_file.parent / src_file.stem
        for c in (base / f"{name}.rs", base / name / "mod.rs"):
            if c.is_file():
                out.append(c)
                break
        return out
    crate_dir = src_file.parent
    while crate_dir != crate_dir.parent and not (crate_dir / "Cargo.toml").exists():
        crate_dir = crate_dir.parent
    src_root = crate_dir / "src"
    parts = [p for p in name.replace("{", " ").split("::") if p and p.isidentifier()]
    for n in range(len(parts), 0, -1):
        rel = Path(*parts[:n])
        for c in (src_root / rel.with_suffix(".rs"), src_root / rel / "mod.rs"):
            if c.is_file():
                return [c]
    return out


def build_symbol_context(tid: str, tasks: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Build 1-hop AST symbol & interface map from task inputs, dependencies' outputs, and local imports."""
    task = tasks.get(tid, {})
    candidate_patterns = list(task.get("inputs", []))
    for dep_id in task.get("depends", []):
        dep_task = tasks.get(dep_id, {})
        candidate_patterns.extend(dep_task.get("outputs", []))

    resolved_files: Set[Path] = set()
    for pat in candidate_patterns:
        if pat == "docs/design/DESIGN.md":
            continue
        p = REPO_ROOT / pat
        if p.exists() and p.is_file():
            resolved_files.add(p)
        elif p.exists() and p.is_dir():
            for sub in p.rglob("*"):
                if sub.is_file() and sub.suffix in (".py", ".ts", ".js", ".rs", ".go", ".h"):
                    resolved_files.add(sub)
        else:
            for g in glob.glob(str(REPO_ROOT / pat), recursive=True):
                gp = Path(g)
                if gp.is_file():
                    resolved_files.add(gp)

    symbol_map: Dict[str, List[str]] = {}
    hop1_modules: Set[str] = set()
    for f in sorted(resolved_files):
        rel = f.relative_to(REPO_ROOT).as_posix()
        sigs, imps = extract_file_symbols(f)
        if sigs:
            symbol_map[rel] = sigs
        for imp in imps:
            if imp.startswith(("rust-mod:", "rust-use:")):
                for cand in rust_module_candidates(f, imp):
                    crel = cand.relative_to(REPO_ROOT).as_posix()
                    if crel not in symbol_map and cand not in resolved_files:
                        csigs, _ = extract_file_symbols(cand)
                        if csigs:
                            symbol_map[f"{crel} (1-hop import)"] = csigs
            else:
                hop1_modules.add(imp)

    for mod in sorted(hop1_modules):
        mod_rel = mod.replace(".", "/") + ".py"
        for candidate in (REPO_ROOT / mod_rel, REPO_ROOT / "src" / mod_rel):
            if candidate.exists() and candidate.is_file():
                rel = candidate.relative_to(REPO_ROOT).as_posix()
                if rel not in symbol_map:
                    sigs, _ = extract_file_symbols(candidate)
                    if sigs:
                        symbol_map[f"{rel} (1-hop import)"] = sigs

    return symbol_map


def cmd_context(args):
    """Compile deterministic context + 1-hop AST symbol signatures for a task."""
    tid = args.id.upper()
    tasks = load_tasks()
    if tid not in tasks:
        print(f"ERROR: Task '{tid}' not found.", file=sys.stderr)
        sys.exit(1)

    t = tasks[tid]
    sym_map = build_symbol_context(tid, tasks)

    if getattr(args, "symbols_only", False):
        if not sym_map:
            return
        lines = []
        for f_rel, sigs in sym_map.items():
            lang = "rust" if f_rel.split(" ")[0].endswith(".rs") else "python"
            lines.append(f"## {f_rel}\n```{lang}\n" + "\n".join(sigs) + "\n```")
        print("\n\n".join(lines))
        return

    payload = {
        "id": tid,
        "file": t.get("file"),
        "rung": t.get("rung", 1),
        "depends": t.get("depends", []),
        "inputs": t.get("inputs", []),
        "outputs": t.get("outputs", []),
        "owns": t.get("owns", []),
        "locked_tests": t.get("locked_tests", []),
        "symbols": sym_map,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"=== Context for {tid} ({t.get('file')}) ===")
        print(f"Depends: {', '.join(payload['depends']) or 'None'}")
        print(f"Inputs:  {', '.join(payload['inputs']) or 'None'}")
        print(f"Owns:    {', '.join(payload['owns']) or 'None'}")
        if payload["locked_tests"]:
            print(f"Locked Tests: {', '.join(payload['locked_tests'])}")
        if sym_map:
            print("\nExtracted Symbol Signatures (Inputs + 1-Hop Imports):")
            for f_rel, sigs in sym_map.items():
                print(f"  [{f_rel}]")
                for s in sigs:
                    for line in s.splitlines():
                        print(f"    {line}")


def cmd_goldfish(args):
    """Pre-seal static specification & isolation audit."""
    tasks = load_tasks()
    findings: List[Dict[str, str]] = []

    for tid, t in sorted(tasks.items()):
        deps = set(t.get("depends", []))
        inputs = set(t.get("inputs", []))
        owns = list(t.get("owns", []))
        outputs = list(t.get("outputs", []))
        body = t.get("body", "")

        for dep in deps:
            dep_outs = set(tasks.get(dep, {}).get("outputs", []))
            if dep_outs and not dep_outs.intersection(inputs):
                findings.append({
                    "persona": "Isolation-Goldfish",
                    "severity": "ERROR",
                    "task": tid,
                    "issue": f"Depends on {dep} but does not list any of {dep}'s outputs ({sorted(dep_outs)}) in 'inputs:'.",
                })

        for out in outputs:
            matched = any(globs_overlap(out, pat) for pat in owns)
            if not matched:
                findings.append({
                    "persona": "Boundary-Goldfish",
                    "severity": "ERROR",
                    "task": tid,
                    "issue": f"Declared output '{out}' is not covered by any pattern in 'owns:' ({owns}).",
                })

        owns_src = any(p.startswith("src") for p in owns)
        owns_tests = any(p.startswith("tests") for p in owns)
        locked = t.get("locked_tests", [])
        if owns_src and owns_tests and not locked:
            findings.append({
                "persona": "Verification-Goldfish",
                "severity": "INFO",
                "task": tid,
                "issue": "Worker owns both 'src/**' and 'tests/**' without 'locked_tests:'.",
            })

        ambiguous_matches = re.findall(r"\b(TBD|TODO|FIXME)\b", body)
        if ambiguous_matches:
            findings.append({
                "persona": "Ambiguity-Goldfish",
                "severity": "ERROR",
                "task": tid,
                "issue": f"Spec prose contains unresolved placeholders ({sorted(set(ambiguous_matches))}).",
            })

    err_count = sum(1 for f in findings if f["severity"] == "ERROR")

    if getattr(args, "json", False):
        print(json.dumps({"findings": findings, "error_count": err_count}, indent=2))
        sys.exit(1 if err_count > 0 else 0)

    print(f"=== Running Goldfish Spec & Boundary Audit across {len(tasks)} workpackages ===")
    if not findings:
        print("  ✓ GOLDFISH AUDIT PASSED: No isolation, boundary, or ambiguity defects found.")
    else:
        for f in findings:
            print(f"  [{f['severity']}] ({f['persona']}) {f['task']}: {f['issue']}")
        if err_count > 0:
            sys.exit(1)


def cmd_question(args):
    """Record a mid-flight blocking question in external state (and untracked mirror) without dirtying git."""
    tid = args.id.upper()
    question_text = args.question
    state = load_state()
    if tid not in state.get("blocked", []):
        state.setdefault("blocked", []).append(tid)
    state.setdefault("questions", {})[tid] = {
        "question": question_text,
        "asked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "OPEN",
    }
    save_state(state)
    journal_event("question", {"task_id": tid, "question": question_text})
    print(f"Recorded blocking question for {tid} in state (see STATUS_MIRROR.json) and marked {tid} BLOCKED.")


def cmd_unblock(args):
    """Clear BLOCKED and running state for a task (or --all) so it can be re-run."""
    tid = args.id.upper() if getattr(args, "id", None) else None
    state = load_state()
    if getattr(args, "all", False) or tid == "ALL":
        state["blocked"] = []
        state["running"] = []
        state["attempts"] = {}
        for q in state.get("questions", {}).values():
            q["status"] = "RESOLVED"
        save_state(state)
        journal_event("unblock", {"task_id": "ALL"})
        print("Cleared all blocked and stale running tasks and reset attempt counters.")
        return

    if not tid:
        print("ERROR: Specify a task ID or --all.", file=sys.stderr)
        sys.exit(1)
    if tid in state.get("blocked", []):
        state["blocked"].remove(tid)
    if tid in state.get("running", []):
        state["running"].remove(tid)
    if tid in state.get("questions", {}):
        state["questions"][tid]["status"] = "RESOLVED"
    # Reset attempts counter so the unblocked task gets a fresh retry budget
    state.get("attempts", {}).pop(tid, None)
    save_state(state)
    journal_event("unblock", {"task_id": tid})
    print(f"Task {tid} unblocked and attempt counter reset.")


def is_agent_installed(agent_spec: str) -> bool:
    """Check whether the CLI binary for an agent spec is installed."""
    if agent_spec == "fake":
        return True
    base = agent_spec.split(":")[0]
    if base == "claude-or":
        return shutil.which("claude-or") is not None or (
            shutil.which("claude") is not None and bool(os.environ.get("OPENROUTER_API_KEY"))
        )
    if base == "claude-local":
        return shutil.which("claude-local") is not None or (
            shutil.which("claude") is not None and shutil.which("ollama") is not None
        )
    bin_map = {
        "pi-code": "pi",
        "pi-devstral": "pi",
        "pi-fw": "pi",
    }
    binary = bin_map.get(base, base)
    return shutil.which(binary) is not None


def is_agent_allowed_by_profile(agent_spec: str, profile: str) -> bool:
    """Enforce routing_profile allowlist strictly."""
    base = agent_spec.split(":")[0]
    if base == "fake":
        return True
    if profile == "local-only":
        return base in ("claude-local", "pi-code", "pi-devstral")
    if profile == "sensitive":
        return not (base.startswith("claude-or") or base == "pi-fw" or "openrouter" in base)
    return True


def get_fallback_agents(rung: int, profile: str) -> List[str]:
    """Return ordered list of installed, profile-permitted [preferred] + fallbacks for (rung, profile)."""
    env_override = os.environ.get("AGENTWORK_DEFAULT_AGENT")
    if env_override:
        return [env_override]

    raw_agents: List[str] = []
    if AGENTS_CONFIG.exists():
        content = AGENTS_CONFIG.read_text(encoding="utf-8")
        prof_section = re.search(rf"{profile}:(.*?)(?=\n  [a-z-]+:|\Z)", content, re.DOTALL)
        if prof_section:
            r_section = re.search(rf"{rung}:(.*?)(?=\n      \d+:|\Z)", prof_section.group(1), re.DOTALL)
            if r_section:
                pref = re.search(r"preferred:\s*['\"]?([a-zA-Z0-9_:.-]+)['\"]?", r_section.group(1))
                if pref:
                    raw_agents.append(pref.group(1))
                fb_match = re.search(r"fallbacks:\s*\[(.*?)\]", r_section.group(1))
                if fb_match:
                    for item in fb_match.group(1).split(","):
                        ag = item.strip().strip('"').strip("'")
                        if ag and ag not in raw_agents:
                            raw_agents.append(ag)

    permitted = [ag for ag in raw_agents if is_agent_allowed_by_profile(ag, profile)]
    installed = [ag for ag in permitted if is_agent_installed(ag)]
    if installed:
        return installed
    for std_cli in ("claude", "codex", "agy", "gemini", "claude-local", "pi-code"):
        if is_agent_allowed_by_profile(std_cli, profile) and is_agent_installed(std_cli):
            return [std_cli]
    return permitted if permitted else ["claude-local" if profile == "local-only" else "claude"]


def resolve_agent_for_rung(rung: int, profile: str) -> str:
    return get_fallback_agents(rung, profile)[0]


def cmd_run(args):
    """Run a single workpackage attempt after verifying the seal."""
    tid = args.id.upper()
    tasks = load_tasks()
    if tid not in tasks:
        print(f"ERROR: Task '{tid}' not found.", file=sys.stderr)
        sys.exit(1)

    verify_seal_or_exit(tasks, require_manifest=True)

    cfg = parse_project_config()
    profile = cfg["routing_profile"]
    worker_timeout = cfg["worker_timeout_seconds"]
    task = tasks[tid]

    rung = args.rung if args.rung is not None else task.get("rung", 1)
    agent = args.agent or resolve_agent_for_rung(rung, profile)

    if not is_agent_allowed_by_profile(agent, profile):
        print(f"SECURITY ERROR: Agent '{agent}' is forbidden under profile '{profile}'.", file=sys.stderr)
        sys.exit(2)

    print(f"=== Running Task {tid} with {agent} (Rung {rung}, Profile: {profile}) ===")

    state = load_state()
    state["running"] = list(set(state.get("running", []) + [tid]))
    save_state(state)
    journal_event("claim", {"task_id": tid, "agent": agent, "rung": rung})

    cmd = self_cmd("launch", tid, agent)
    if args.retry_note_file:
        cmd.extend(["--retry-note-file", args.retry_note_file])

    rc = 1
    try:
        rc = run_process_with_group_timeout(cmd, cwd=REPO_ROOT, timeout_sec=worker_timeout)
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT: Worker for {tid} exceeded {worker_timeout}s limit.", file=sys.stderr)
        rc = 124
    finally:
        # Always clear running state even on Ctrl-C / SIGTERM / exception
        state = load_state()
        if tid in state.get("running", []):
            state["running"].remove(tid)
        save_state(state)

    state = load_state()
    if rc == 0:
        wt_dir = get_trees_dir() / tid
        head_res = subprocess.run(["git", "-C", str(wt_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head_res.returncode == 0 and head_res.stdout.strip():
            state.setdefault("verified_heads", {})[tid] = head_res.stdout.strip()
        journal_event("run_success", {"task_id": tid, "agent": agent, "head": state.get("verified_heads", {}).get(tid)})
        print(f"Task {tid} run PASSED mechanical checks.")
        save_state(state)
        sys.exit(0)
    else:
        journal_event("run_fail", {"task_id": tid, "agent": agent, "exit_code": rc})
        print(f"Task {tid} run FAILED (exit {rc}).", file=sys.stderr)
        save_state(state)
        sys.exit(rc)


def cmd_check(args):
    """Verify task gate."""
    tid = args.id.upper()
    cfg = parse_project_config()
    wt_dir = get_trees_dir() / tid
    target = wt_dir if wt_dir.exists() else REPO_ROOT

    try:
        rc = run_process_with_group_timeout(self_cmd("gate", tid, str(target)), cwd=REPO_ROOT, timeout_sec=cfg["gate_timeout_seconds"])
        sys.exit(rc)
    except subprocess.TimeoutExpired:
        print(f"GATE TIMEOUT: Gate check for {tid} exceeded {cfg['gate_timeout_seconds']}s.", file=sys.stderr)
        sys.exit(9)


def cmd_merge(args):
    """Verify candidate commit against pre-merge base SHA, integrate, and re-verify."""
    tid = args.id.upper()
    tasks = load_tasks()
    verify_seal_or_exit(tasks, require_manifest=True)

    cfg = parse_project_config()
    gate_timeout = cfg["gate_timeout_seconds"]
    wt_dir = get_trees_dir() / tid
    branch = f"wp/{tid.lower()}"

    print(f"=== Promoting Task {tid} to Base Branch ===")
    journal_event("start_merge", {"task_id": tid, "branch": branch})

    # 1. Require clean main tree
    status_res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True)
    if status_res.stdout.strip():
        print("ERROR: Main repo working tree is dirty; aborting promotion.", file=sys.stderr)
        print(status_res.stdout, file=sys.stderr)
        sys.exit(13)

    # 2. Record pre-merge base SHA and candidate branch SHA
    base_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    if base_res.returncode != 0 or not base_res.stdout.strip():
        print("ERROR: Could not resolve base HEAD SHA.", file=sys.stderr)
        sys.exit(13)
    pre_merge_base_sha = base_res.stdout.strip()

    # Check if THIS task's worktree had a pre-seeded baseline commit since pre_merge_base_sha
    effective_gate_base = pre_merge_base_sha
    parent_res = subprocess.run(
        ["git", "log", f"{pre_merge_base_sha}..{branch}", f"--grep=^chore(seed): pre-seed files for {tid}$", "-n", "1", "--format=%H"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if parent_res.returncode == 0 and parent_res.stdout.strip():
        effective_gate_base = parent_res.stdout.strip()

    cand_res = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=REPO_ROOT, capture_output=True, text=True)
    if cand_res.returncode != 0 or not cand_res.stdout.strip():
        print(f"ERROR: Candidate branch '{branch}' does not exist.", file=sys.stderr)
        sys.exit(1)
    candidate_sha = cand_res.stdout.strip()

    state = load_state()
    expected_sha = state.get("verified_heads", {}).get(tid)
    if expected_sha and expected_sha != candidate_sha:
        print(f"SECURITY ERROR: Candidate branch '{branch}' ({candidate_sha}) does not match verified worker commit ({expected_sha}).", file=sys.stderr)
        sys.exit(14)

    # 3. Pre-merge gate check on candidate worktree if present
    if wt_dir.exists():
        try:
            pre_rc = run_process_with_group_timeout(self_cmd("gate", tid, str(wt_dir), effective_gate_base), cwd=REPO_ROOT, timeout_sec=gate_timeout)
        except subprocess.TimeoutExpired:
            pre_rc = 9
        if pre_rc != 0:
            print(f"FAIL: Candidate worktree for {tid} failed pre-merge verification.", file=sys.stderr)
            sys.exit(1)

    # 4. Merge candidate commit with --no-ff
    merge_res = subprocess.run(["git", "merge", "--no-ff", candidate_sha, "-m", f"chore(agentwork): integrate {tid}"], cwd=REPO_ROOT)
    if merge_res.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=REPO_ROOT, capture_output=True)
        print(f"CONFLICT: Merge conflict integrating {tid}.", file=sys.stderr)
        journal_event("merge_conflict", {"task_id": tid})
        sys.exit(1)

    # 5. Post-merge verification gate passing explicit effective_gate_base
    try:
        post_rc = run_process_with_group_timeout(self_cmd("gate", tid, str(REPO_ROOT), effective_gate_base), cwd=REPO_ROOT, timeout_sec=gate_timeout)
    except subprocess.TimeoutExpired:
        post_rc = 9
    if post_rc != 0:
        subprocess.run(["git", "reset", "--keep", pre_merge_base_sha], cwd=REPO_ROOT)
        print("FAIL: Integrated commit failed post-merge verification; rolled back to pre-merge base.", file=sys.stderr)
        journal_event("merge_post_verify_fail", {"task_id": tid})
        sys.exit(1)

    # 6. Mark DONE in authoritative state
    state = load_state()
    if tid not in state.get("completed", []):
        state["completed"].append(tid)
    save_state(state)
    journal_event("promoted", {"task_id": tid, "commit": candidate_sha})

    # 7. Remove worktree
    if wt_dir.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=REPO_ROOT, capture_output=True)

    print(f"Task {tid} successfully promoted and marked DONE.")
    sys.exit(0)


def inspect_report_question(tid: str) -> Optional[str]:
    """Inspect exact worker report in active worktree for a mid-flight ## Question section."""
    wt_dir = get_trees_dir() / tid
    rep_file = wt_dir / "agentwork" / "reports" / f"{tid.lower()}.md"
    if not rep_file.exists():
        return None
    text = rep_file.read_text(encoding="utf-8", errors="ignore")
    m_q = re.search(r"^##\s+Question\s*\n+(.*?)(?=\n##\s+|\Z)", text, re.MULTILINE | re.DOTALL)
    if m_q and m_q.group(1).strip():
        return m_q.group(1).strip()
    return None


def cmd_drive(args):
    """Autonomous execution engine with process-level lock, budgets, timeouts, and seal verification."""
    sdir, _, _, drive_lock, _ = state_paths(create=True)
    attempts_dir = sdir / "attempts"
    drive_lock_obj = FileLock(drive_lock, exclusive=True, blocking=False)
    try:
        drive_lock_obj.acquire()
    except LockBusy:
        print("ERROR: Another 'pipeline.py drive' instance is already running for this repository.", file=sys.stderr)
        sys.exit(13)

    print("=== Starting Autonomous Driver Engine ===")
    cfg = parse_project_config()
    profile = cfg["routing_profile"]
    max_wall_clock = cfg["global_timeout_seconds"]
    max_attempts = cfg["max_total_attempts_per_wp"]
    retries_per_rung = max(1, cfg["max_retries_per_rung"])
    gate_timeout = cfg["gate_timeout_seconds"]
    start_time = time.time()

    # Recover any stale 'running' tasks left behind by a previously killed process
    init_state = load_state()
    if init_state.get("running"):
        print(f"Recovering stale running tasks from interrupted session: {init_state['running']}")
        init_state["running"] = []
        save_state(init_state)

    while True:
        if time.time() - start_time > max_wall_clock:
            print("BUDGET EXHAUSTED: Global wall-clock limit reached.", file=sys.stderr)
            sys.exit(11)

        tasks = load_tasks()
        if not tasks:
            print("STALLED: No workpackages found in agentwork/packages/.", file=sys.stderr)
            sys.exit(12)

        verify_seal_or_exit(tasks, require_manifest=True)

        state = load_state()
        statuses = compute_task_statuses(tasks, state)

        # Check completion
        if all(s == "DONE" for s in statuses.values()):
            print("\n=== ALL WORKPACKAGES COMPLETE: Running final acceptance gate ===")
            try:
                final_rc = run_process_with_group_timeout(self_cmd("gate", "FINAL", str(REPO_ROOT)), cwd=REPO_ROOT, timeout_sec=gate_timeout)
            except subprocess.TimeoutExpired:
                final_rc = 9
            if final_rc == 0:
                print("=== SUCCESS: All workpackages integrated and verified. ===")
                sys.exit(0)
            else:
                print("FAIL: Final acceptance gate failed.", file=sys.stderr)
                sys.exit(10)

        # Find next ready task
        ready = [t for tid, t in sorted(tasks.items()) if statuses.get(tid) == "READY"]
        if not ready:
            if any(s.startswith("BLOCKED") for s in statuses.values()):
                print("BLOCKED: No ready tasks remain; one or more tasks are blocked.", file=sys.stderr)
                sys.exit(10)
            else:
                print("STALLED: No tasks are ready and none are blocked (check dependency graph).", file=sys.stderr)
                sys.exit(12)

        cur = ready[0]
        tid = cur["id"]
        base_rung = int(cur.get("rung", 1))
        attempts_map = state.setdefault("attempts", {})
        task_attempts = attempts_map.setdefault(tid, [])

        attempt_idx = len(task_attempts)
        if attempt_idx >= max_attempts:
            state["blocked"] = list(set(state.get("blocked", []) + [tid]))
            save_state(state)
            print(f"Task {tid} exhausted max attempts ({max_attempts}) and was marked BLOCKED.", file=sys.stderr)
            continue

        cur_rung = min(3, base_rung + (attempt_idx // retries_per_rung))
        within_rung_idx = attempt_idx % retries_per_rung
        candidates = get_fallback_agents(cur_rung, profile)
        if getattr(args, "agent", None):
            chosen_agent = args.agent
        else:
            chosen_agent = candidates[within_rung_idx % len(candidates)]

        print(f"\n--- Autopilot driving {tid} (Attempt {attempt_idx + 1}/{max_attempts}, Rung {cur_rung}, Agent {chosen_agent}) ---")
        run_cmd = self_cmd("run", tid, "--rung", str(cur_rung), "--agent", chosen_agent)

        retry_note = attempts_dir / f"{tid}-retry.md"
        if task_attempts and retry_note.exists():
            run_cmd.extend(["--retry-note-file", str(retry_note)])

        run_res = subprocess.run(run_cmd, cwd=REPO_ROOT)
        state = load_state()
        state.setdefault("attempts", {}).setdefault(tid, []).append({
            "agent": chosen_agent,
            "rung": cur_rung,
            "rc": run_res.returncode,
            "ts": time.time(),
        })
        save_state(state)

        # Check if worker emitted a mid-flight Question in its active worktree report
        question_body = inspect_report_question(tid)
        if question_body:
            cmd_question(argparse.Namespace(id=tid, question=question_body))
            continue

        if run_res.returncode == 0:
            merge_res = subprocess.run(self_cmd("merge", tid), cwd=REPO_ROOT)
            if merge_res.returncode != 0:
                print(f"Promotion failed for {tid}; marking {tid} BLOCKED and continuing independent tasks.", file=sys.stderr)
                state = load_state()
                state["blocked"] = list(set(state.get("blocked", []) + [tid]))
                save_state(state)
                continue
        else:
            log_dir = REPO_ROOT / "agentwork" / "logs"
            logs = sorted(log_dir.glob(f"{tid}-*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.exists() else []
            if logs:
                tail = "\n".join(logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()[-40:])
                retry_note.write_text(f"Previous attempt ({chosen_agent}, Rung {cur_rung}) failed:\n```\n{tail}\n```\n", encoding="utf-8")


def cmd_status(args):
    tasks = load_tasks()
    state = load_state()
    statuses = compute_task_statuses(tasks, state)

    total = len(tasks)
    done_count = sum(1 for s in statuses.values() if s == "DONE")

    out = {
        "total": total,
        "completed": state.get("completed", []),
        "ready": [tid for tid, s in statuses.items() if s == "READY"],
        "blocked": [tid for tid, s in statuses.items() if s.startswith("BLOCKED")],
        "running": state.get("running", []),
        "questions": state.get("questions", {}),
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print("=== Autonomous Pipeline Status ===")
        print(f"Progress: {done_count} / {total} tasks completed ({done_count * 100 // max(1, total)}%)")
        print(f"Ready:     {', '.join(out['ready']) or 'None'}")
        print(f"Completed: {', '.join(out['completed']) or 'None'}")
        print(f"Blocked:   {', '.join(out['blocked']) or 'None'}")
        open_qs = {k: v for k, v in out["questions"].items() if v.get("status") == "OPEN"}
        if open_qs:
            print("Questions:")
            for k, v in open_qs.items():
                print(f"  - {k}: {v.get('question')}")


PASSTHROUGH = {
    # subcommand: (module, function) — these parse their own arguments
    "gate": ("awlib.gate", "cli_main"),
    "launch": ("awlib.launch", "cli_main"),
    "split": ("awlib.split", "cli_main"),
    "driver": ("awlib.driver", "cli_main"),
    "setup": ("awlib.setup_cmd", "setup_main"),
    "update-all": ("awlib.setup_cmd", "update_all_main"),
    "rust-check": ("awlib.rust_checks", "cli_main"),
}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in PASSTHROUGH:
        import importlib

        mod_name, fn_name = PASSTHROUGH[sys.argv[1]]
        fn = getattr(importlib.import_module(mod_name), fn_name)
        sys.exit(fn(sys.argv[2:]))

    parser = argparse.ArgumentParser(
        description="Deterministic Multi-Agent Pipeline Driver",
        epilog="Also: gate | launch | split | driver | setup | update-all | rust-check (each has its own --help).")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="Run diagnostics")
    subparsers.add_parser("validate", help="Validate DAG and specs")
    subparsers.add_parser("seal", help="Compute seal and write manifest")
    subparsers.add_parser("reseal", help="Re-seal updated DAG into a new manifest generation")

    p_drive = subparsers.add_parser("drive", help="Run autonomous execution engine")
    p_drive.add_argument("--agent", help="Optional agent override for all tasks in this drive run")

    p_list = subparsers.add_parser("list", help="List all tasks")
    p_list.add_argument("--json", action="store_true")

    p_next = subparsers.add_parser("next", help="Show next runnable task")
    p_next.add_argument("--json", action="store_true")

    p_run = subparsers.add_parser("run", help="Run task")
    p_run.add_argument("id")
    p_run.add_argument("--agent", help="Override agent")
    p_run.add_argument("--rung", type=int, help="Override rung")
    p_run.add_argument("--retry-note-file", help="Path to repair notes file")

    p_check = subparsers.add_parser("check", help="Verify task gate")
    p_check.add_argument("id")

    p_merge = subparsers.add_parser("merge", help="Integrate task into base")
    p_merge.add_argument("id")

    p_status = subparsers.add_parser("status", help="Show status")
    p_status.add_argument("--json", action="store_true")

    p_context = subparsers.add_parser("context", help="Compile task context and 1-hop AST symbol signatures")
    p_context.add_argument("id")
    p_context.add_argument("--json", action="store_true")
    p_context.add_argument("--symbols-only", action="store_true")

    p_goldfish = subparsers.add_parser("goldfish", help="Run static specification & boundary audit")
    p_goldfish.add_argument("--json", action="store_true")

    p_question = subparsers.add_parser("question", help="Record blocking question and pause task")
    p_question.add_argument("id")
    p_question.add_argument("question")

    p_unblock = subparsers.add_parser("unblock", help="Clear blocked/running status for a task (or --all)")
    p_unblock.add_argument("id", nargs="?")
    p_unblock.add_argument("--all", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "doctor": cmd_doctor,
        "validate": cmd_validate,
        "seal": cmd_seal,
        "reseal": cmd_seal,
        "drive": cmd_drive,
        "list": cmd_list,
        "next": cmd_next,
        "run": cmd_run,
        "check": cmd_check,
        "merge": cmd_merge,
        "status": cmd_status,
        "context": cmd_context,
        "goldfish": cmd_goldfish,
        "question": cmd_question,
        "unblock": cmd_unblock,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
