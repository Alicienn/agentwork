"""pipeline.py commands (validate / seal / list / context / doctor) and setup / update-all."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import TEMPLATE, git, install_harness, write

SPEC = """---
id: {id}
depends: [{deps}]
inputs:
  - src/lib.rs
outputs:
  - {out}
owns:
  - {out}
done_when:
  - cargo test
---
# {id}
Verify: cargo test
"""


def add_spec(proj, tid, out, deps=""):
    write(proj.root / "agentwork" / "packages" / f"{tid}-x.md", SPEC.format(id=tid, deps=deps, out=out))


def test_validate_seal_and_drift(make_project):
    proj = make_project(files={"Cargo.toml": "[package]\nname='x'\n", "src/lib.rs": "pub fn f() {}\n"})
    add_spec(proj, "WP01", "src/a.rs")
    add_spec(proj, "WP02", "src/b.rs", deps="WP01")
    assert proj.run("validate").returncode == 0
    s = proj.run("seal")
    assert s.returncode == 0, s.stdout + s.stderr
    manifest = json.loads((proj.root / "agentwork/config/harness-manifest.json").read_text(encoding="utf-8"))
    assert manifest["tasks"]["WP01"]["file"] == "agentwork/packages/WP01-x.md"  # posix paths even on Windows
    assert manifest["gate_hash"]
    proj.commit("seal")

    # CRLF re-checkout of a spec must NOT count as drift (Windows core.autocrlf)
    spec = proj.root / "agentwork/packages/WP01-x.md"
    spec.write_bytes(spec.read_bytes().replace(b"\n", b"\r\n"))
    r = proj.run("run", "WP01", "--agent", "fake", extra_env={"AGENTWORK_DEFAULT_AGENT": "fake"})
    assert "SEALED INPUT CHANGED" not in r.stderr
    proj.git("checkout", "--", ".")

    # A real edit of an upstream spec drifts the dependent too
    write(spec, spec.read_text(encoding="utf-8") + "\nmore words\n")
    r = proj.run("run", "WP02", "--agent", "fake")
    assert r.returncode == 14 and "SEALED INPUT CHANGED" in r.stderr


def test_validate_catches_overlap_cycle_and_missing_verify(make_project):
    proj = make_project()
    add_spec(proj, "WP01", "src/a.rs", deps="WP02")
    add_spec(proj, "WP02", "src/a.rs", deps="WP01")
    write(proj.root / "agentwork/packages/WP03-x.md", "---\nid: WP03\nowns: [src/c.rs]\n---\n# no verify\n")
    r = proj.run("validate")
    assert r.returncode == 1
    assert "Cycle detected" in r.stdout and "missing a deterministic" in r.stdout
    assert proj.run("seal").returncode == 1


def test_list_next_status_unblock_json(make_project):
    proj = make_project()
    add_spec(proj, "WP01", "src/a.rs")
    out = json.loads(proj.run("list", "--json").stdout)
    assert out[0]["id"] == "WP01" and out[0]["status"] == "READY"
    nxt = json.loads(proj.run("next", "--json").stdout)
    assert nxt["action"] == "run" and nxt["argv"][0] == sys.executable
    assert proj.run("question", "WP01", "what now?").returncode == 0
    st = json.loads(proj.run("status", "--json").stdout)
    assert st["blocked"] == ["WP01"]
    assert proj.run("unblock", "--all").returncode == 0
    assert json.loads(proj.run("status", "--json").stdout)["blocked"] == []


def test_context_extracts_rust_signatures(make_project):
    proj = make_project(files={
        "Cargo.toml": "[package]\nname='x'\n",
        "src/lib.rs": "pub mod model;\npub use model::Thing;\npub fn api(x: u8) -> Result<u8, String> { Ok(x) }\nfn hidden() {}\n",
        "src/model.rs": "pub struct Thing { pub id: u32 }\nimpl Thing { pub fn new(id: u32) -> Self { Thing { id } } }\npub trait Store { fn put(&mut self, t: Thing); }\npub enum Kind { A, B }\n",
    })
    add_spec(proj, "WP01", "src/a.rs")
    r = proj.run("context", "WP01", "--symbols-only")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "```rust" in out
    assert "pub fn api(x: u8) -> Result<u8, String>;" in out
    assert "hidden" not in out
    assert "src/model.rs (1-hop import)" in out
    assert "pub struct Thing { pub id: u32 .. }" in out
    assert "impl Thing {" in out and "pub fn new(id: u32) -> Self;" in out
    assert "pub trait Store {" in out and "pub enum Kind { A, B }" in out


def test_doctor_runs(make_project):
    proj = make_project()
    r = proj.run("doctor")
    assert "Pipeline Doctor Diagnostics" in r.stdout
    assert "python found" in r.stdout
    assert "Sandbox selftest passed" in r.stdout or "Sandbox selftest failed" in r.stdout


def test_sensitive_profile_blocks_openrouter(make_project):
    proj = make_project(profile="sensitive")
    r = proj.run("launch", "WP01", "claude-or")
    assert r.returncode == 2 and "SECURITY ERROR" in r.stderr
    settings = json.loads((proj.root / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Bash(claude-or*)" in settings["permissions"]["deny"]


def test_setup_installs_and_is_idempotent(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    git(target, "init", "-q")
    install_harness(target)
    for rel in ("agentwork/pipeline.py", "agentwork/awlib/gate.py", "agentwork/scripts/verify_gate.sh",
                "agentwork/scripts/verify_gate.cmd", "agentwork/scripts/_python.cmd",
                "agentwork/tools/rust-testcheck/src/main.rs", "agentwork/config/project.yaml", ".gitattributes"):
        assert (target / rel).is_file(), rel
    cfg = (target / "agentwork/config/project.yaml").read_text(encoding="utf-8")
    assert 'routing_profile: "balanced"' in cfg and 'isolation_mode: "auto"' in cfg
    assert 'repo_id: ""' not in cfg
    assert (target / "agentwork/scripts/verify_gate.cmd").read_bytes().count(b"\r\n") >= 3
    assert b"\r\n" not in (target / "agentwork/scripts/verify_gate.sh").read_bytes()
    if os.name != "nt":
        assert os.access(target / "agentwork/scripts/verify_gate.sh", os.X_OK)

    # second run: nothing created, nothing updated
    res = install_harness(target)
    assert "0 created, 0 updated" in res.stdout

    # local edits to a managed file are kept, --force overwrites with a backup
    agents = target / "AGENTS.md"
    write(agents, "my own rules\n")
    res = install_harness(target)
    assert "KEEP:   AGENTS.md" in res.stdout and agents.read_text(encoding="utf-8") == "my own rules\n"
    r = subprocess.run([sys.executable, str(TEMPLATE / "agentwork/pipeline.py"), "setup", str(target), "--force"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and (target / "AGENTS.md.bak").is_file()

    # seeded files are never overwritten
    ctx = target / "agentwork/CONTEXT.md"
    write(ctx, "project brief\n")
    install_harness(target)
    assert ctx.read_text(encoding="utf-8") == "project brief\n"


def test_setup_requires_profile(tmp_path):
    r = subprocess.run([sys.executable, str(TEMPLATE / "agentwork/pipeline.py"), "setup", str(tmp_path / "n")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "--profile is required" in r.stderr


def test_update_all_finds_projects(tmp_path):
    root = tmp_path / "Code"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        git(root / name, "init", "-q")
        install_harness(root / name)
    (root / "a-agent-trees" / "WP01" / "agentwork" / "config").mkdir(parents=True)
    r = subprocess.run([sys.executable, str(TEMPLATE / "agentwork/pipeline.py"), "update-all", "--dry-run",
                        "--roots", str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Found 2 project(s)" in r.stdout


def test_shell_launchers_run(tmp_path):
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", str(TEMPLATE / "agentwork" / "scripts" / "verify_gate.cmd")],
                           capture_output=True, text=True)
    else:
        r = subprocess.run(["sh", str(TEMPLATE / "agentwork" / "scripts" / "verify_gate.sh")], capture_output=True, text=True)
    assert r.returncode == 1 and "Usage: pipeline.py gate" in r.stderr
