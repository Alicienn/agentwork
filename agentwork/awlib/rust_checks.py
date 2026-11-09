"""Rust anti-cheat checks for the mechanical gate.

Two interchangeable analyzers produce the same finding kinds:

* ``syn``     — ``agentwork/tools/rust-testcheck`` (a real Rust parser), built on
                demand with cargo into the user cache directory;
* ``builtin`` — :mod:`awlib.rustsrc` (pure-Python lexer + item parser).

``rust.checker: auto`` prefers ``syn`` and falls back to ``builtin`` with a
warning when the helper cannot be built (e.g. offline first run).

Only *new* problems count: each file is analyzed at the base commit and at the
candidate commit, and a finding is reported only if the same (kind, test,
fingerprint) did not already exist at base. Removing a test that existed at
base is reported too (``verification.forbid_test_removal``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import rustsrc
from .oscompat import IS_WINDOWS, FileLock, user_cache_root

TOOL_REL = Path("agentwork") / "tools" / "rust-testcheck"
FINDING_KINDS = ("ignored", "empty", "no_assertion", "todo", "unimplemented", "cfg_disabled", "lint_allow")


@dataclass
class Analysis:
    tests: List[Tuple[str, int, str]]  # (qualname, line, fingerprint)
    findings: List[Tuple[str, str, int, str, str]]  # (kind, qualname, line, fp, message)
    parse_error: Optional[str] = None


class CheckerUnavailable(Exception):
    pass


def is_rust_test_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return "tests" in parts[:-1]


# --------------------------------------------------------------------------- #
# syn helper
# --------------------------------------------------------------------------- #

def _tool_hash(tool_dir: Path) -> str:
    h = hashlib.sha256()
    for rel in ("Cargo.toml", "Cargo.lock", "src/main.rs"):
        p = tool_dir / rel
        if p.exists():
            h.update(rel.encode())
            h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def build_syn_checker(repo: Path, quiet: bool = True) -> Path:
    """Build (once per source hash) and return the path of the syn helper binary."""
    override = os.environ.get("AGENTWORK_RUST_TESTCHECK_BIN")
    if override:
        p = Path(override)
        if not p.is_file():
            raise CheckerUnavailable(f"AGENTWORK_RUST_TESTCHECK_BIN={override} does not exist")
        return p
    tool_dir = repo / TOOL_REL
    if not (tool_dir / "Cargo.toml").exists():
        raise CheckerUnavailable(f"{tool_dir} not found")
    cargo = shutil.which("cargo")
    if not cargo:
        raise CheckerUnavailable("cargo not found in PATH")
    cache = user_cache_root() / "rust-testcheck" / _tool_hash(tool_dir)
    exe = cache / "target" / "release" / ("agentwork-rust-testcheck" + (".exe" if IS_WINDOWS else ""))
    if exe.is_file():
        return exe
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(cache / "build.lock", timeout=900):
        if exe.is_file():
            return exe
        cmd = [cargo, "build", "--release", "--manifest-path", str(tool_dir / "Cargo.toml"),
               "--target-dir", str(cache / "target")]
        if (tool_dir / "Cargo.lock").exists():
            cmd.append("--locked")
        env = dict(os.environ)
        env.pop("RUSTFLAGS", None)  # the project's -D warnings etc. must not break the helper
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode != 0 or not exe.is_file():
            tail = "\n".join((res.stderr or "").splitlines()[-15:])
            raise CheckerUnavailable(f"cargo build of rust-testcheck failed:\n{tail}")
    if not quiet:
        print(f"rust-testcheck built: {exe}")
    return exe


def _parse_tool_output(out: str) -> Analysis:
    tests, findings, perr = [], [], None
    for line in out.splitlines():
        parts = line.split("\t")
        if parts[0] == "TEST" and len(parts) >= 4:
            tests.append((parts[1], int(parts[2] or 0), parts[3]))
        elif parts[0] == "FIND" and len(parts) >= 6:
            findings.append((parts[1], parts[2], int(parts[3] or 0), parts[4], "\t".join(parts[5:])))
        elif parts[0] == "PARSE_ERROR":
            perr = f"line {parts[1] if len(parts) > 1 else '?'}: {parts[2] if len(parts) > 2 else ''}"
    return Analysis(tests, findings, perr)


def analyze_with_syn(exe: Path, src: str, is_test_file: bool, extra: Sequence[str]) -> Analysis:
    fd, tmp = tempfile.mkstemp(suffix=".rs", prefix="aw-rs-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        cmd = [str(exe)]
        if is_test_file:
            cmd.append("--test-file")
        for n in extra:
            cmd += ["--assert-name", n]
        cmd.append(tmp)
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise CheckerUnavailable(f"rust-testcheck exited {res.returncode}: {res.stderr.strip()}")
        return _parse_tool_output(res.stdout)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def analyze_with_builtin(src: str, is_test_file: bool, extra: Sequence[str]) -> Analysis:
    try:
        tests, findings = rustsrc.analyze_source(src, is_test_file, extra)
    except rustsrc.RustParseError as e:
        return Analysis([], [], str(e))
    return Analysis([(t.qualname, t.line, t.fingerprint) for t in tests],
                    [(f.kind, f.qualname, f.line, f.fingerprint, f.message) for f in findings])


class RustAnalyzer:
    def __init__(self, repo: Path, mode: str = "auto", extra_assert_names: Sequence[str] = ()):
        self.repo = repo
        self.extra = list(extra_assert_names or [])
        self.mode = (mode or "auto").lower()
        self.warnings: List[str] = []
        self.exe: Optional[Path] = None
        if self.mode not in ("auto", "syn", "builtin"):
            raise ValueError(f"rust.checker must be auto|syn|builtin, got {mode!r}")
        if self.mode in ("auto", "syn"):
            try:
                self.exe = build_syn_checker(repo)
                self.used = "syn"
            except CheckerUnavailable as e:
                if self.mode == "syn":
                    raise
                self.warnings.append(f"syn-based rust-testcheck unavailable ({str(e).splitlines()[0]}); "
                                     f"using the builtin Python analyzer.")
                self.used = "builtin"
        else:
            self.used = "builtin"

    def analyze(self, src: str, is_test_file: bool) -> Analysis:
        if self.exe is not None:
            return analyze_with_syn(self.exe, src, is_test_file, self.extra)
        return analyze_with_builtin(src, is_test_file, self.extra)


# --------------------------------------------------------------------------- #
# Gate entry point
# --------------------------------------------------------------------------- #

def git_show(cwd: Path, rev: str, rel: str) -> Optional[str]:
    res = subprocess.run(["git", "-C", str(cwd), "show", f"{rev}:{rel}"], capture_output=True)
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")


def check_rust_changes(analyzer: RustAnalyzer, target: Path, base: str, head: str, changed: Sequence[str],
                       forbid_test_removal: bool = True, forbid_new_allows: bool = True) -> List[str]:
    errors: List[str] = []
    base_tests: Set[str] = set()
    head_tests: Set[str] = set()
    for rel in changed:
        if not rel.endswith(".rs"):
            continue
        is_test = is_rust_test_path(rel)
        old = git_show(target, base, rel)
        new = git_show(target, head, rel)
        a_old = analyzer.analyze(old, is_test) if old is not None else Analysis([], [])
        a_new = analyzer.analyze(new, is_test) if new is not None else Analysis([], [])
        if a_new.parse_error:
            errors.append(f"Rust file '{rel}' does not parse: {a_new.parse_error}")
            continue
        base_tests.update(t[0] for t in a_old.tests)
        head_tests.update(t[0] for t in a_new.tests)
        old_keys = {(f[0], f[1], f[3]) for f in a_old.findings}
        for kind, qual, line, fp, msg in a_new.findings:
            if (kind, qual, fp) in old_keys:
                continue
            if kind == "lint_allow" and not forbid_new_allows:
                continue
            loc = f"{rel}:{line}" if line else rel
            errors.append(f"[{kind}] {loc}: {msg}")
    if forbid_test_removal:
        removed = sorted(base_tests - head_tests)
        if removed:
            errors.append("Rust tests removed or renamed (verification.forbid_test_removal): " + ", ".join(removed))
    return errors


def cli_main(argv: Sequence[str]) -> int:
    """``pipeline.py rust-check [--checker auto|syn|builtin] [--test-file] FILE...`` — debug helper."""
    import argparse

    ap = argparse.ArgumentParser(prog="pipeline.py rust-check")
    ap.add_argument("--checker", default="auto")
    ap.add_argument("--test-file", action="store_true")
    ap.add_argument("--assert-name", action="append", default=[])
    ap.add_argument("files", nargs="+")
    ns = ap.parse_args(list(argv))
    repo = Path(__file__).resolve().parent.parent.parent
    an = RustAnalyzer(repo, ns.checker, ns.assert_name)
    for w in an.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    rc = 0
    for f in ns.files:
        src = Path(f).read_text(encoding="utf-8")
        a = an.analyze(src, ns.test_file or is_rust_test_path(Path(f).as_posix()))
        print(f"== {f} ({an.used})")
        if a.parse_error:
            print(f"  PARSE ERROR {a.parse_error}")
            rc = 1
        for t in a.tests:
            print(f"  test {t[0]} (line {t[1]})")
        for k, q, line, _fp, msg in a.findings:
            print(f"  [{k}] line {line}: {msg}")
            rc = 1
    return rc
