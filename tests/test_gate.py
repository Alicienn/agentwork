"""Mechanical gate on small throw-away repositories (no agent, no worktree)."""

from __future__ import annotations

import sys

import pytest

from conftest import commit_all, git, requires_cargo, write

PY = sys.executable.replace("\\", "/")
PYQ = f'"{PY}"' if " " in PY else PY  # frontmatter list items lose surrounding quotes

SPEC_PY = f"""---
id: WP01
owns:
  - src/calc.py
  - tests/test_calc.py
locked_tests:
  - tests/test_locked.py
done_when:
  - {PYQ} -m pytest -q -p no:cacheprovider tests
---
# WP01
Verify: "{PY}" -c "print('verify ok')"
"""

CALC_TEST = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
LOCKED_TEST = "from calc import add\n\ndef test_locked():\n    assert add(2, 2) == 4\n"


@pytest.fixture
def pyproj(make_project):
    proj = make_project(files={
        "pyproject.toml": "[project]\nname='x'\nversion='0'\n",
        "src/calc.py": "def add(a, b):\n    raise NotImplementedError\n",
        "tests/test_locked.py": LOCKED_TEST,
        "agentwork/packages/WP01-calc.md": SPEC_PY,
    })
    proj.base = proj.git("rev-parse", "HEAD").stdout.strip()
    return proj


def gate(proj, *extra):
    return proj.run("gate", "WP01", str(proj.root), proj.base, *extra)


def good_change(proj):
    write(proj.root / "src/calc.py", "def add(a, b):\n    return a + b\n")
    write(proj.root / "tests/test_calc.py", CALC_TEST)
    write(proj.root / "agentwork/reports/wp01.md", "# WP01 report\n")


def test_gate_passes(pyproj):
    good_change(pyproj)
    pyproj.commit("work")
    r = gate(pyproj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verify ok" in r.stdout and "GATE PASS" in r.stdout


def test_spec_missing(pyproj):
    r = pyproj.run("gate", "WP99", str(pyproj.root), pyproj.base)
    assert r.returncode == 2


def test_dirty_tree(pyproj):
    good_change(pyproj)
    assert gate(pyproj).returncode == 3


def test_no_commits(pyproj):
    r = gate(pyproj)
    assert r.returncode == 4 and "no new commits" in r.stderr


def test_bad_base(pyproj):
    good_change(pyproj)
    pyproj.commit("work")
    r = pyproj.run("gate", "WP01", str(pyproj.root), "0" * 40)
    assert r.returncode == 4


@pytest.mark.parametrize("path", ["agentwork/config/agents.yaml", ".claude/settings.json", "pyproject.toml",
                                  "agentwork/awlib/evil.py", "conftest.py", ".github/workflows/x.yml"])
def test_protected(pyproj, path):
    good_change(pyproj)
    write(pyproj.root / path, "tampered\n")
    pyproj.commit("work")
    r = gate(pyproj)
    assert r.returncode == 5, r.stderr
    assert path in r.stderr


def test_outside_owns(pyproj):
    good_change(pyproj)
    write(pyproj.root / "README.md", "x\n")
    pyproj.commit("work")
    r = gate(pyproj)
    assert r.returncode == 6 and "README.md" in r.stderr


def test_missing_report(pyproj):
    write(pyproj.root / "src/calc.py", "def add(a, b):\n    return a + b\n")
    pyproj.commit("work")
    assert gate(pyproj).returncode == 7


def test_locked_test_modified_or_deleted_or_renamed(pyproj):
    good_change(pyproj)
    write(pyproj.root / "tests/test_locked.py", LOCKED_TEST + "\n")
    pyproj.commit("work")
    assert gate(pyproj).returncode == 8
    pyproj.git("reset", "-q", "--hard", pyproj.base)
    good_change(pyproj)
    pyproj.git("mv", "tests/test_locked.py", "tests/test_calc_locked_renamed.py")
    pyproj.commit("rename")
    r = gate(pyproj)
    assert r.returncode == 8, r.stderr  # --no-renames: the old locked path shows up as deleted


@pytest.mark.parametrize("body, needle", [
    ("import pytest\n\n@pytest.mark.skip\ndef test_add():\n    assert False\n", "skip/xfail decorator"),
    ("def test_add():\n    pass\n", "empty stub"),
    ("def test_add():\n    ...\n", "empty stub"),
    ("import pytest\npytestmark = pytest.mark.skip\n\ndef test_add():\n    assert 1\n", "pytestmark"),
    ("import pytest\n\ndef test_add():\n    pytest.skip('later')\n", "pytest.skip"),
])
def test_python_tampering(pyproj, body, needle):
    good_change(pyproj)
    write(pyproj.root / "tests/test_calc.py", body)
    pyproj.commit("work")
    r = gate(pyproj)
    assert r.returncode == 8, r.stdout + r.stderr
    assert needle in r.stderr


def test_python_test_removal(pyproj):
    good_change(pyproj)
    pyproj.commit("work")
    pyproj.base = pyproj.git("rev-parse", "HEAD").stdout.strip()
    write(pyproj.root / "tests/test_calc.py", "from calc import add\n\ndef test_other():\n    assert add(0, 0) == 0\n")
    write(pyproj.root / "agentwork/reports/wp01.md", "# WP01 report v2\n")
    pyproj.commit("rename test")
    r = gate(pyproj)
    assert r.returncode == 8 and "test_add" in r.stderr


def test_verify_command_failure(pyproj):
    write(pyproj.root / "src/calc.py", "def add(a, b):\n    return a - b\n")
    write(pyproj.root / "tests/test_calc.py", CALC_TEST)
    write(pyproj.root / "agentwork/reports/wp01.md", "# r\n")
    pyproj.commit("buggy")
    r = gate(pyproj)
    assert r.returncode == 9, r.stdout + r.stderr


def test_final_gate_uses_project_commands(pyproj):
    py = pyproj.root / "agentwork/config/project.yaml"
    text = py.read_text(encoding="utf-8").replace('  test_command: ""\n  lint_command: ""\n  # Shell',
                                                  f'  test_command: "\\"{PY}\\" -c \\"raise SystemExit(4)\\""\n  lint_command: ""\n  # Shell', 1)
    write(py, text)
    pyproj.commit("cfg")
    r = pyproj.run("gate", "FINAL", str(pyproj.root))
    assert r.returncode == 9, r.stdout + r.stderr
    assert "test_command" in r.stderr


# --------------------------------------------------------------------------- #
# Rust specifics without cargo-heavy work (Verify: is a trivial python command)
# --------------------------------------------------------------------------- #

SPEC_RS = f"""---
id: WP01
owns:
  - src/lib.rs
  - tests/extra.rs
  - crates/core/Cargo.toml
locked_tests:
  - tests/acceptance.rs
---
# WP01
Verify: "{PY}" -c "print('verify ok')"
"""


@pytest.fixture
def rsproj(make_project):
    def patch(text):
        return text.replace('checker: "auto"', 'checker: "builtin"')
    proj = make_project(files={
        "Cargo.toml": "[package]\nname='d'\nversion='0.1.0'\nedition='2021'\n",
        "crates/core/Cargo.toml": "[package]\nname='c'\n",
        "src/lib.rs": "pub fn f() -> u8 { 1 }\n",
        "tests/acceptance.rs": "#[test] fn a() { assert_eq!(d::f(), 1); }\n",
        "tests/extra.rs": "#[test] fn keep_me() { assert_eq!(d::f(), 1); }\n#[test] fn legacy_no_assert() { d::f(); }\n",
        "agentwork/packages/WP01-x.md": SPEC_RS,
    }, project_yaml_patch=patch)
    proj.base = proj.git("rev-parse", "HEAD").stdout.strip()
    write(proj.root / "agentwork/reports/wp01.md", "# r\n")
    return proj


def test_rust_preexisting_findings_are_not_blamed(rsproj):
    # legacy_no_assert already existed at base: editing another part of the file is fine.
    write(rsproj.root / "tests/extra.rs", (rsproj.root / "tests/extra.rs").read_text(encoding="utf-8")
          + "#[test] fn new_one() { assert!(d::f() > 0); }\n")
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "analyzer = builtin" in r.stdout


def test_rust_modified_test_is_rechecked(rsproj):
    write(rsproj.root / "tests/extra.rs", "#[test] fn keep_me() { d::f(); }\n#[test] fn legacy_no_assert() { d::f(); }\n")
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 8 and "keep_me" in r.stderr


def test_rust_test_removal(rsproj):
    write(rsproj.root / "tests/extra.rs", "#[test] fn legacy_no_assert() { d::f(); }\n")
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 8 and "removed or renamed" in r.stderr and "keep_me" in r.stderr


def test_rust_removal_check_can_be_disabled(make_project):
    def patch(text):
        return text.replace('checker: "auto"', 'checker: "builtin"').replace("forbid_test_removal: true", "forbid_test_removal: false")
    proj = make_project(files={
        "Cargo.toml": "[package]\nname='d'\n",
        "tests/extra.rs": "#[test] fn keep_me() { assert!(true); }\n",
        "agentwork/packages/WP01-x.md": SPEC_RS,
    }, project_yaml_patch=patch)
    proj.base = proj.git("rev-parse", "HEAD").stdout.strip()
    write(proj.root / "tests/extra.rs", "#[test] fn other() { assert!(true); }\n")
    write(proj.root / "agentwork/reports/wp01.md", "# r\n")
    proj.commit("work")
    r = gate(proj)
    assert r.returncode == 0, r.stdout + r.stderr


def test_rust_guarded_files_need_literal_owns(rsproj):
    write(rsproj.root / "crates/core/Cargo.toml", "[package]\nname='c'\nversion='1'\n")  # listed literally: allowed
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 0, r.stdout + r.stderr
    write(rsproj.root / "Cargo.toml", "[package]\nname='d'\n[lints.clippy]\nall='allow'\n")
    rsproj.commit("work2")
    r = gate(rsproj)
    assert r.returncode == 5 and "Cargo.toml" in r.stderr


@pytest.mark.parametrize("rel", ["clippy.toml", ".cargo/config.toml", "build.rs", "rust-toolchain.toml"])
def test_rust_guarded_lint_and_build_config(rsproj, rel):
    write(rsproj.root / rel, "x = 1\n")
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 5, r.stdout + r.stderr


def test_rust_unparsable_test_file(rsproj):
    write(rsproj.root / "tests/extra.rs", "#[test] fn keep_me() { assert!(true); \n#[test] fn legacy_no_assert() {}\n")
    rsproj.commit("work")
    r = gate(rsproj)
    assert r.returncode == 8 and "does not parse" in r.stderr


@requires_cargo
def test_rust_gate_with_syn_checker(make_project):
    def patch(text):
        return text.replace('checker: "auto"', 'checker: "syn"')
    proj = make_project(files={
        "Cargo.toml": "[package]\nname='d'\n",
        "agentwork/packages/WP01-x.md": SPEC_RS,
    }, project_yaml_patch=patch)
    proj.base = proj.git("rev-parse", "HEAD").stdout.strip()
    write(proj.root / "tests/extra.rs", "#[test]\n#[cfg_attr(unix, ignore)]\nfn t() { assert!(true); }\n")
    write(proj.root / "agentwork/reports/wp01.md", "# r\n")
    proj.commit("work")
    r = gate(proj)
    assert r.returncode == 8, r.stdout + r.stderr
    assert "analyzer = syn" in r.stdout and "[ignored]" in r.stderr
