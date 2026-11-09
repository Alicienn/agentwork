"""Scripted stand-in for a coding agent, used by the end-to-end tests.

launch runs:  python fake_worker.py <WP_ID> <WORKTREE> <BRANCH>
The scenario comes from the AW_SCENARIO environment variable.
"""

import os
import subprocess
import sys
from pathlib import Path

GOOD_IMPL = '''//! Tiny demo crate used by the agentwork end-to-end tests.

/// Sum all values, returning `None` on overflow.
pub fn checked_sum(values: &[i64]) -> Option<i64> {
    values.iter().try_fold(0i64, |acc, v| acc.checked_add(*v))
}
'''

EXTRA_OK = '''use rust_demo::checked_sum;

#[test]
fn negative_values() {
    assert_eq!(checked_sum(&[-1, -2]), Some(-3));
}
'''

EXTRA_IGNORED = '''use rust_demo::checked_sum;

#[test]
#[ignore]
fn slow_case() {
    assert_eq!(checked_sum(&[1]), Some(1));
}
'''

EXTRA_EMPTY = '''#[test]
fn looks_like_a_test() {}
'''

EXTRA_NO_ASSERT = '''use rust_demo::checked_sum;

#[test]
fn calls_but_checks_nothing() {
    let _ = checked_sum(&[1, 2]);
}
'''

EXTRA_TODO = '''#[test]
fn later() {
    todo!()
}
'''

EXTRA_CFG_OFF = '''use rust_demo::checked_sum;

#[cfg(any())]
#[test]
fn hidden() {
    assert_eq!(checked_sum(&[1]), Some(2));
}
'''


def write(wt: Path, rel: str, text: str) -> None:
    p = wt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main() -> int:
    wp_id, wt = sys.argv[1], Path(sys.argv[2])
    scenario = os.environ.get("AW_SCENARIO", "ok")
    report = f"agentwork/reports/{wp_id.lower()}.md"
    if scenario == "no_commit":
        return 0

    write(wt, "src/lib.rs", GOOD_IMPL)
    write(wt, report, f"# {wp_id}\n\nScenario: {scenario}\n")

    if scenario == "ok":
        write(wt, "tests/extra.rs", EXTRA_OK)
    elif scenario == "outside_owns":
        write(wt, "README.md", "changed by the agent\n")
    elif scenario == "locked_modified":
        p = wt / "tests" / "acceptance.rs"
        write(wt, "tests/acceptance.rs", p.read_text(encoding="utf-8").replace("Some(6)", "Some(6) /* tweaked */"))
    elif scenario == "locked_deleted":
        (wt / "tests" / "acceptance.rs").unlink()
    elif scenario == "ignored":
        write(wt, "tests/extra.rs", EXTRA_IGNORED)
    elif scenario == "empty_test":
        write(wt, "tests/extra.rs", EXTRA_EMPTY)
    elif scenario == "no_assert":
        write(wt, "tests/extra.rs", EXTRA_NO_ASSERT)
    elif scenario == "todo_test":
        write(wt, "tests/extra.rs", EXTRA_TODO)
    elif scenario == "cfg_off":
        write(wt, "tests/extra.rs", EXTRA_CFG_OFF)
    elif scenario == "protected_config":
        p = wt / "agentwork" / "config" / "project.yaml"
        write(wt, "agentwork/config/project.yaml", p.read_text(encoding="utf-8") + "\n# agent was here\n")
    elif scenario == "cargo_toml":
        p = wt / "Cargo.toml"
        write(wt, "Cargo.toml", p.read_text(encoding="utf-8") + "\n[lints.rust]\nwarnings = \"allow\"\n")
    elif scenario == "crate_allow":
        write(wt, "src/lib.rs", "#![allow(clippy::all)]\n" + GOOD_IMPL)
    elif scenario == "escape_sandbox":
        target = Path(os.environ["AW_ESCAPE_TARGET"])
        try:
            target.write_text("escaped", encoding="utf-8")
            write(wt, "tests/extra.rs", EXTRA_OK)
            print("ESCAPE: write outside the worktree SUCCEEDED")
        except OSError as e:
            print(f"ESCAPE: write outside the worktree BLOCKED ({e})")
    else:
        print(f"unknown scenario {scenario}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", f"feat: {wp_id} ({scenario})"], check=True, env=env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
