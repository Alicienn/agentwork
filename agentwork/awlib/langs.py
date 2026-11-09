"""Per-language default commands and the optional cargo-mutants check."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .oscompat import IS_WINDOWS, run_shell_command


def _quote(p: str) -> str:
    return subprocess.list2cmdline([p]) if IS_WINDOWS else shlex.quote(p)


def has_nextest() -> bool:
    if not shutil.which("cargo"):
        return False
    try:
        res = subprocess.run(["cargo", "nextest", "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def rust_test_command(cfg: Dict[str, Any]) -> str:
    explicit = str(cfg.get("rust", {}).get("test_command") or "").strip()
    if explicit:
        return explicit
    return "cargo nextest run" if has_nextest() else "cargo test"


def project_check_commands(cfg: Dict[str, Any], langs: List[str]) -> List[Tuple[str, str]]:
    """(label, command) pairs for the FINAL gate (and per-task when enabled).

    ``verification.test_command`` / ``lint_command`` win when set (historic
    behaviour). Otherwise each enabled language contributes its defaults.
    """
    v = cfg.get("verification", {})
    out: List[Tuple[str, str]] = []
    lint = str(v.get("lint_command") or "").strip()
    test = str(v.get("test_command") or "").strip()
    if lint:
        out.append(("lint_command", lint))
    else:
        if "rust" in langs:
            r = cfg.get("rust", {})
            for key in ("fmt_command", "clippy_command"):
                c = str(r.get(key) or "").strip()
                if c:
                    out.append((f"rust.{key}", c))
        if "python" in langs:
            c = str(cfg.get("python", {}).get("lint_command") or "").strip()
            if c:
                out.append(("python.lint_command", c))
    if test:
        out.append(("test_command", test))
    else:
        if "rust" in langs:
            out.append(("rust.test_command", rust_test_command(cfg)))
        if "python" in langs:
            c = str(cfg.get("python", {}).get("test_command") or "").strip()
            if c:
                out.append(("python.test_command", c))
    return out


# --------------------------------------------------------------------------- #
# cargo-mutants
# --------------------------------------------------------------------------- #

def _count_lines(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(1 for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


def read_mutants_outcome(out_dir: Path) -> Dict[str, int]:
    """Count outcomes from a cargo-mutants ``mutants.out`` directory (text files are stable API)."""
    d = out_dir / "mutants.out" if (out_dir / "mutants.out").is_dir() else out_dir
    return {k: _count_lines(d / f"{k}.txt") for k in ("caught", "missed", "timeout", "unviable")}


def mutants_score(counts: Dict[str, int], count_timeouts_as_caught: bool = True) -> Tuple[Optional[float], int, int]:
    caught = counts.get("caught", 0) + (counts.get("timeout", 0) if count_timeouts_as_caught else 0)
    missed = counts.get("missed", 0) + (0 if count_timeouts_as_caught else counts.get("timeout", 0))
    total = caught + missed
    if total == 0:
        return None, caught, total
    return caught / total, caught, total


def run_mutants(target: Path, base: str, head: str, mcfg: Dict[str, Any], env: Dict[str, str],
                shell: str = "auto") -> Tuple[bool, str]:
    """Run cargo-mutants on the diff base..head. Returns (passed, message)."""
    cmd_bin = os.environ.get("AGENTWORK_CARGO_MUTANTS") or "cargo mutants"
    min_ratio = float(mcfg.get("min_kill_ratio", 0.8))
    timeout = float(mcfg.get("timeout_seconds", 3600))
    count_to = bool(mcfg.get("count_timeouts_as_caught", True))
    extra = str(mcfg.get("extra_args") or "").strip()

    diff = subprocess.run(["git", "-C", str(target), "diff", "--no-renames", base, head, "--", "*.rs"],
                          capture_output=True)
    if diff.returncode != 0:
        return False, "git diff for cargo-mutants failed"
    if not diff.stdout.strip():
        return True, "cargo-mutants: no Rust changes to mutate"
    work = Path(tempfile.mkdtemp(prefix="aw-mutants-"))
    diff_file = work / "changes.diff"
    diff_file.write_bytes(diff.stdout)
    out_dir = work / "out"
    command = f"{cmd_bin} --in-diff {_quote(str(diff_file))} --output {_quote(str(out_dir))} --no-shuffle"
    if extra:
        command += " " + extra
    print(f"Running cargo-mutants (min kill ratio {min_ratio:.0%}): {command}")
    try:
        rc = run_shell_command(command, cwd=target, env=env, timeout=timeout, preference=shell)
    except subprocess.TimeoutExpired:
        return False, f"cargo-mutants exceeded {timeout:.0f}s"
    # cargo-mutants: 0 = all caught, 2 = missed, 3 = timeouts, 1/4 = usage error or baseline failure.
    if rc in (1, 4):
        return False, f"cargo-mutants failed to run (exit {rc}); the unmutated tree must build and pass its tests"
    counts = read_mutants_outcome(out_dir)
    ratio, caught, total = mutants_score(counts, count_to)
    if ratio is None:
        return True, f"cargo-mutants: no viable mutants in the diff ({counts})"
    msg = f"cargo-mutants: {caught}/{total} mutants caught ({ratio:.0%}, minimum {min_ratio:.0%}); details: {counts}"
    return ratio + 1e-9 >= min_ratio, msg
