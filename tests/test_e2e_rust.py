"""End-to-end: the harness installed in examples/rust-demo, one work package, a fake agent.

Covers the full cycle (validate -> seal -> run -> gate -> merge -> drive/FINAL) and
every cheating attempt the gate must stop.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES, requires_cargo, write

pytestmark = requires_cargo

FAKE = str(FIXTURES / "fake_worker.py")


@pytest.fixture
def demo(make_project, tmp_path):
    proj = make_project(example="rust-demo")
    proj.env["CARGO_TARGET_DIR"] = str(tmp_path / "cargo-target")
    proj.env["FAKE_WORKER_SCRIPT"] = FAKE
    v = proj.run("validate")
    assert v.returncode == 0, v.stdout + v.stderr
    s = proj.run("seal")
    assert s.returncode == 0, s.stdout + s.stderr
    proj.commit("seal")
    return proj


def run_wp(proj, scenario: str, **extra):
    env = {"AW_SCENARIO": scenario}
    env.update(extra)
    return proj.run("run", "WP01", "--agent", "fake", extra_env=env)


def test_full_cycle_passes_and_merges(demo):
    r = run_wp(demo, "ok")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "GATE PASS: Task WP01" in out
    assert "Rust test checks: analyzer =" in out

    m = demo.run("merge", "WP01")
    assert m.returncode == 0, m.stdout + m.stderr
    assert "checked_add" in (demo.root / "src" / "lib.rs").read_text(encoding="utf-8")

    # All packages DONE -> drive runs the FINAL gate (fmt + clippy + tests) and succeeds.
    d = demo.run("drive", "--agent", "fake")
    out = d.stdout + d.stderr
    assert d.returncode == 0, out
    assert "GATE PASS: FINAL" in out
    assert "cargo fmt" in out and "cargo clippy" in out


@pytest.mark.parametrize("scenario, code, needle", [
    ("outside_owns", 6, "outside declared ownership"),
    ("locked_modified", 8, "locked acceptance test file 'tests/acceptance.rs'"),
    ("locked_deleted", 8, "locked acceptance test file 'tests/acceptance.rs'"),
    ("ignored", 8, "[ignored]"),
    ("empty_test", 8, "[empty]"),
    ("no_assert", 8, "[no_assertion]"),
    ("todo_test", 8, "[todo]"),
    ("cfg_off", 8, "[cfg_disabled]"),
    ("crate_allow", 8, "[lint_allow]"),
    ("protected_config", 5, "Protected file was modified: agentwork/config/project.yaml"),
    ("cargo_toml", 5, "without being listed explicitly in owns: Cargo.toml"),
    ("no_commit", 4, "no new commits"),
])
def test_gate_rejects_cheating(demo, scenario, code, needle):
    r = run_wp(demo, scenario)
    out = r.stdout + r.stderr
    assert r.returncode == code, out
    assert needle in out, out
    assert "GATE FAIL" in out


def test_spec_modified_after_seal_is_refused(demo):
    spec = demo.root / "agentwork" / "packages" / "WP01-checked-sum.md"
    write(spec, spec.read_text(encoding="utf-8").replace("Implement `checked_sum`", "Implement `checked_sum` quickly"))
    demo.commit("tamper with spec")
    r = run_wp(demo, "ok")
    assert r.returncode == 14, r.stdout + r.stderr
    assert "SEALED INPUT CHANGED" in r.stderr
    # drive refuses too
    d = demo.run("drive", "--agent", "fake")
    assert d.returncode == 14, d.stdout + d.stderr


def test_merge_refuses_unverified_branch_change(demo):
    r = run_wp(demo, "ok")
    assert r.returncode == 0, r.stdout + r.stderr
    wt = Path(demo.env["AGENT_TREES"]) / "WP01"
    write(wt / "src" / "lib.rs", (wt / "src" / "lib.rs").read_text(encoding="utf-8") + "\n// sneaky\n")
    from conftest import commit_all
    commit_all(wt, "sneaky change after the gate")
    m = demo.run("merge", "WP01")
    assert m.returncode == 14, m.stdout + m.stderr
    assert "does not match verified worker commit" in m.stderr


def test_windows_or_unsandboxed_run_warns(demo):
    """worktree-only (Windows, or no sandbox) must say so loudly for real agents."""
    r = demo.run("launch", "--selftest")
    assert r.returncode == 0, r.stdout + r.stderr
    if sys.platform == "win32":
        assert "DEGRADED" in r.stdout
        assert "NO OS SANDBOX" in r.stderr


def _sandbox_available() -> bool:
    import shutil
    import subprocess
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True)
        return r.returncode == 0
    return False


@pytest.mark.skipif(not _sandbox_available(), reason="no usable Seatbelt/bwrap on this machine")
def test_os_sandbox_blocks_writes_outside_worktree(demo):
    # The main checkout is outside the agent's worktree: it must be read-only for the agent.
    outside = demo.root / "escaped-from-sandbox.txt"
    r = run_wp(demo, "escape_sandbox", AGENTWORK_SANDBOX_FAKE="1", AW_ESCAPE_TARGET=str(outside))
    out = r.stdout + r.stderr
    assert "isolation: worktree-seatbelt" in out, out
    assert "ESCAPE: write outside the worktree BLOCKED" in out, out
    assert not outside.exists()


def _have_cargo_mutants() -> bool:
    import subprocess
    try:
        return subprocess.run(["cargo", "mutants", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _have_cargo_mutants(), reason="cargo-mutants not installed")
@pytest.mark.parametrize("ratio, expected_rc", [("0.5", 0), ("1.01", 16)])
def test_cargo_mutants_threshold_real(make_project, tmp_path, ratio, expected_rc):
    def patch(text):
        return (text.replace("    enabled: false\n    min_kill_ratio: 0.8", f"    enabled: true\n    min_kill_ratio: {ratio}")
                .replace('extra_args: ""', 'extra_args: "-j 2"'))
    proj = make_project(example="rust-demo", project_yaml_patch=patch)
    proj.env["CARGO_TARGET_DIR"] = str(tmp_path / "cargo-target")
    proj.env["FAKE_WORKER_SCRIPT"] = FAKE
    assert proj.run("seal").returncode == 0
    proj.commit("seal")
    r = run_wp(proj, "ok")
    out = r.stdout + r.stderr
    assert r.returncode == expected_rc, out
    assert "cargo-mutants:" in out and "mutants caught" in out
