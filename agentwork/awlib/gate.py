"""Layer-1 mechanical verification gate (portable replacement for verify_gate.sh).

    pipeline.py gate <TASK_ID> [WORKTREE_DIR] [BASE_SHA]

Exit codes (unchanged from the bash version, plus 15/16):
  0  pass
  1  usage error
  2  task spec not found
  3  uncommitted changes
  4  no commits / bad base / empty diff
  5  protected or guarded file modified
  6  file modified outside owns:
  7  durable report missing
  8  test tampering (locked test changed, skip/ignore, empty test, removed test, ...)
  9  Verify:/done_when/project command failed
  15 configuration error (project.yaml)
  16 cargo-mutants kill ratio below threshold
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config as awconfig
from . import langs as awlangs
from .oscompat import merged_env, prepend_pythonpath, run_shell_command
from .python_checks import check_python_changes
from .rust_checks import RustAnalyzer, check_rust_changes
from .specs import find_task_spec, is_literal_pattern, matches_any, normalize_rel, parse_frontmatter, verify_line

BUILTIN_PROTECTED = [
    "docs/design/DESIGN.md",
    "agentwork/config/**",
    "agentwork/state/DECISIONS.md",
    ".claude/**",
    ".codex/**",
    ".agents/**",
    "agentwork/pipeline.py",
    "agentwork/tui.py",
    "agentwork/awlib/**",
    "agentwork/tools/**",
    "agentwork/scripts/**",
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
    ".gitattributes",
    ".github/**",
]


class GateFail(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _git(cwd: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def canonical_repo(target: Path) -> Path:
    res = _git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip()).resolve().parent
    res = _git(target, "rev-parse", "--show-toplevel")
    if res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip()).resolve()
    return target.resolve()


def changed_files(target: Path, base: str, head: str) -> List[str]:
    # --no-renames: a renamed/deleted locked test must show up under its old path.
    res = subprocess.run(["git", "-C", str(target), "-c", "core.quotepath=off", "diff", "--name-only",
                          "--no-renames", "-z", base, head], capture_output=True)
    if res.returncode != 0:
        raise GateFail(4, f"git diff failed between {base} and {head}.")
    return [normalize_rel(p) for p in res.stdout.decode("utf-8", errors="replace").split("\0") if p.strip()]


def dirty(target: Path) -> str:
    return _git(target, "status", "--porcelain").stdout.strip()


def _verify_env(target: Path) -> Dict[str, str]:
    env = merged_env()
    return prepend_pythonpath(env, target / "src", target)


def run_commands(cmds: Sequence[str], target: Path, shell: str, label: str = "verification command") -> None:
    env = _verify_env(target)
    for cmd in cmds:
        print(f"Running {label}: {cmd}", flush=True)
        try:
            rc = run_shell_command(cmd, cwd=target, env=env, preference=shell)
        except FileNotFoundError as e:
            raise GateFail(9, f"Cannot run {label} '{cmd}': {e}")
        if rc != 0:
            raise GateFail(9, f"Verification command failed: {cmd} (exit {rc})")


def load_cfg(repo: Path) -> Dict[str, Any]:
    try:
        return awconfig.load_project_config(repo / "agentwork" / "config" / "project.yaml")
    except ValueError as e:
        raise GateFail(15, f"Cannot parse agentwork/config/project.yaml: {e}")


def _languages(cfg: Dict[str, Any], target: Path) -> List[str]:
    try:
        return awconfig.enabled_languages(cfg, target)
    except ValueError as e:
        raise GateFail(15, str(e))


def gate_final(target: Path, repo: Path) -> None:
    print(f"=== Verifying FINAL Repository Acceptance Gate (target: {target}) ===", flush=True)
    d = dirty(target)
    if d:
        raise GateFail(3, "Working tree has uncommitted modifications:\n" + "\n".join("    " + l for l in d.splitlines()))
    cfg = load_cfg(repo)
    shell = str(cfg["verification"].get("shell") or "auto")
    cmds = awlangs.project_check_commands(cfg, _languages(cfg, target))
    for label, cmd in cmds:
        try:
            run_commands([cmd], target, shell, label=f"project {label}")
        except GateFail as e:
            raise GateFail(9, f"Project {label} failed: {cmd}") from e
    print("=== GATE PASS: FINAL repository acceptance checks passed ===")


def gate_task(task_id: str, target: Path, repo: Path, base_sha: Optional[str]) -> None:
    cfg = load_cfg(repo)
    vcfg = cfg["verification"]
    shell = str(vcfg.get("shell") or "auto")

    if not base_sha:
        branch = _git(repo, "branch", "--show-current").stdout.strip()
        base_sha = _git(repo, "rev-parse", branch or "HEAD").stdout.strip()
    print(f"=== Verifying Gate for Task: {task_id} (target: {target}, base: {base_sha or 'unknown'}) ===", flush=True)

    spec = find_task_spec(task_id, [repo / "agentwork" / "packages", repo / "agentwork" / "steps",
                                    target / "agentwork" / "packages"])
    if spec is None:
        raise GateFail(2, f"Spec for task '{task_id}' not found in {repo} or {target}.")
    content = spec.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)

    d = dirty(target)
    if d:
        raise GateFail(3, "Worktree has uncommitted modifications:\n" + "\n".join("    " + l for l in d.splitlines()))

    head = _git(target, "rev-parse", "--verify", "HEAD").stdout.strip()
    if not head:
        raise GateFail(4, "Could not determine HEAD commit.")
    if not base_sha or _git(target, "rev-parse", "--verify", f"{base_sha}^{{commit}}").returncode != 0:
        raise GateFail(4, f"Invalid or missing BASE_SHA '{base_sha}'.")
    if _git(target, "rev-parse", f"{base_sha}^{{commit}}").stdout.strip() == head:
        raise GateFail(4, f"Worker produced no new commits relative to base {base_sha}.")
    changed = changed_files(target, base_sha, head)
    if not changed:
        raise GateFail(4, f"Diff between {base_sha} and {head} is empty.")

    owns = [normalize_rel(p) for p in meta.get("owns", [])]
    locked = [normalize_rel(p) for p in meta.get("locked_tests", [])]
    langs = _languages(cfg, target)

    # Protected files (built-in + project.yaml protected_files:)
    protected = BUILTIN_PROTECTED + [str(p) for p in (cfg.get("protected_files") or [])]
    for f in changed:
        if matches_any(f, protected):
            raise GateFail(5, f"Protected file was modified: {f}")

    # Guarded build/lint configuration: allowed only when listed *literally* in owns.
    if "rust" in langs:
        guarded = [str(p) for p in (cfg["rust"].get("guarded_files") or [])]
        literal_owns = {p for p in owns if is_literal_pattern(p)}
        for f in changed:
            if matches_any(f, guarded) and f not in literal_owns:
                raise GateFail(5, f"Build/lint configuration file was modified without being listed explicitly in owns: {f} "
                                  f"(Cargo.toml, Cargo.lock, build.rs, clippy/rustfmt config, .cargo/, rust-toolchain)")

    for f in changed:
        if matches_any(f, locked):
            raise GateFail(8, f"Worker modified locked acceptance test file '{f}' (protected by locked_tests).")

    report_name = task_id.lower()
    exact_report = f"agentwork/reports/{report_name}.md"
    violating = [f for f in changed if not matches_any(f, owns + [exact_report])]
    if violating:
        raise GateFail(6, "Files modified outside declared ownership:\n" + "\n".join("    " + v for v in violating))
    if exact_report not in [f.lower() for f in changed]:
        raise GateFail(7, f"Durable report '{exact_report}' was not created/updated in this task's commits.")
    rp = target / exact_report
    if not rp.is_file() or rp.stat().st_size == 0:
        raise GateFail(7, f"Durable report '{exact_report}' is missing or empty.")

    forbid_removal = bool(vcfg.get("forbid_test_removal", True))
    errors = check_python_changes(target, base_sha, head, changed, forbid_removal)
    if errors:
        raise GateFail(8, errors[0] if len(errors) == 1 else "Python test tampering:\n" + "\n".join("    " + e for e in errors))

    if "rust" in langs and any(f.endswith(".rs") for f in changed):
        rcfg = cfg["rust"]
        try:
            analyzer = RustAnalyzer(repo, str(rcfg.get("checker") or "auto"), rcfg.get("extra_assertion_names") or [])
        except Exception as e:  # syn explicitly requested but unavailable
            raise GateFail(15, f"Rust checker unavailable: {e}")
        for w in analyzer.warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        print(f"Rust test checks: analyzer = {analyzer.used}", flush=True)
        errors = check_rust_changes(analyzer, target, base_sha, head, changed,
                                    forbid_test_removal=forbid_removal,
                                    forbid_new_allows=bool(rcfg.get("forbid_new_crate_allows", True)))
        if errors:
            raise GateFail(8, "Rust test tampering detected:\n" + "\n".join("    " + e for e in errors))

    cmds: List[str] = []
    vl = verify_line(content)
    if vl:
        cmds.append(vl)
    for dw in meta.get("done_when", []) or []:
        dw = str(dw).strip()
        if dw and dw != vl:
            cmds.append(dw)
    if not cmds:
        raise GateFail(9, f"Task '{task_id}' has no 'Verify:' line or 'done_when:' commands.")
    run_commands(cmds, target, shell)

    if bool(vcfg.get("run_project_checks_per_task", False)):
        for label, cmd in awlangs.project_check_commands(cfg, langs):
            if cmd not in cmds:
                run_commands([cmd], target, shell, label=f"project {label}")

    mcfg = cfg["rust"].get("mutants") or {}
    if "rust" in langs and bool(mcfg.get("enabled", False)):
        ok, msg = awlangs.run_mutants(target, base_sha, head, mcfg, _verify_env(target), shell)
        print(msg, flush=True)
        if not ok:
            raise GateFail(16, msg)

    print(f"=== GATE PASS: Task {task_id} passed all mechanical checks ===")


def run_gate(task_id: str, target_dir: Optional[str] = None, base_sha: Optional[str] = None) -> int:
    tid = task_id.upper()
    target = Path(target_dir or ".").resolve()
    repo = canonical_repo(target)
    try:
        if tid == "FINAL":
            gate_final(target, repo)
        else:
            gate_task(tid, target, repo, base_sha)
        return 0
    except GateFail as e:
        print(f"GATE FAIL: {e}", file=sys.stderr, flush=True)
        return e.code


def cli_main(argv: Sequence[str]) -> int:
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print("Usage: pipeline.py gate <TASK_ID> [WORKTREE_DIR] [BASE_SHA]", file=sys.stderr)
        return 1
    return run_gate(args[0], args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
