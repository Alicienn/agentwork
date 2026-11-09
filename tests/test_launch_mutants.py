"""Launcher decisions (isolation, profiles, prompts) and the cargo-mutants check."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from awlib import langs, launch
from conftest import commit_all, git, write


@pytest.mark.parametrize("mode, sb, expect", [
    ("auto", "bwrap", ("worktree-seatbelt", "bwrap")),
    ("auto", None, ("worktree-only", None)),
    ("worktree-only", "seatbelt", ("worktree-only", None)),
    ("worktree-seatbelt", "seatbelt", ("worktree-seatbelt", "seatbelt")),
])
def test_effective_isolation(monkeypatch, mode, sb, expect):
    monkeypatch.setattr(launch, "available_sandbox", lambda: sb)
    assert launch.effective_isolation(mode) == expect


def test_seatbelt_required_without_sandbox_fails(monkeypatch):
    monkeypatch.setattr(launch, "available_sandbox", lambda: None)
    with pytest.raises(ValueError, match="worktree-seatbelt"):
        launch.effective_isolation("worktree-seatbelt")
    with pytest.raises(ValueError):
        launch.effective_isolation("container-magic")


def test_selftest_degraded_is_loud(monkeypatch, capsys):
    monkeypatch.setattr(launch, "available_sandbox", lambda: None)
    assert launch.selftest("auto") == 0
    cap = capsys.readouterr()
    assert "DEGRADED" in cap.out and "NO OS SANDBOX" in cap.err
    assert launch.selftest("worktree-seatbelt") == 1


@pytest.mark.parametrize("agent, profile, blocked", [
    ("claude-or:nemotron", "sensitive", True), ("pi-fw", "sensitive", True), ("claude", "sensitive", False),
    ("codex", "local-only", True), ("claude-local", "local-only", False), ("fake", "local-only", False),
    ("claude-or", "balanced", False),
])
def test_profiles(agent, profile, blocked):
    assert (launch.check_profile(agent, profile) is not None) is blocked


def test_sandbox_wrappers(tmp_path):
    wt, git_dir, att = tmp_path / "wt", tmp_path / "repo" / ".git", tmp_path / "att"
    for d in (wt, git_dir, att):
        d.mkdir(parents=True)
    sb = launch.wrap_in_sandbox("seatbelt", ["agent", "-p"], wt, git_dir, "WP01", att)
    assert sb[0] == "sandbox-exec" and sb[-2:] == ["agent", "-p"]
    assert "(deny file-write*)" in sb[2] and str(wt.resolve()) in sb[2] and "/.ssh" in sb[2]
    bw = launch.wrap_in_sandbox("bwrap", ["agent"], wt, git_dir, "WP01", att)
    assert bw[:3] == ["bwrap", "--ro-bind", "/"] and bw[-1] == "agent"
    assert str(wt.resolve()) in bw and "--chdir" in bw
    assert launch.wrap_in_sandbox(None, ["agent"], wt, git_dir, "WP01", att) == ["agent"]


def test_long_prompt_is_replaced_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, "IS_WINDOWS", True)
    prompt = "x" * 40000
    cmd = launch.shrink_prompt_for_windows(["codex.exe", "exec", prompt], prompt, tmp_path / "p.md")
    assert prompt not in cmd and "p.md" in cmd[-1]
    short = "do it"
    assert launch.shrink_prompt_for_windows(["codex.exe", short], short, tmp_path / "p.md") == ["codex.exe", short]
    # .cmd shims re-parse arguments: always use the pointer
    assert short not in launch.shrink_prompt_for_windows(["C:/npm/codex.cmd", short], short, tmp_path / "p.md")
    monkeypatch.setattr(launch, "IS_WINDOWS", False)
    assert launch.shrink_prompt_for_windows(["codex", prompt], prompt, tmp_path / "p.md") == ["codex", prompt]


def test_claude_gets_prompt_on_stdin_and_optional_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(launch.shutil, "which", lambda b: f"/bin/{b}")
    cmd, env, stdin = launch.agent_command("claude", "PROMPT", "WP01", tmp_path, "wp/wp01", tmp_path, True)
    assert stdin == "PROMPT" and "PROMPT" not in cmd
    assert env["ANTHROPIC_API_KEY"] is None
    i = cmd.index("--settings")
    assert '"enabled": true' in Path(cmd[i + 1]).read_text(encoding="utf-8")


def test_fake_worker_python_script(monkeypatch, tmp_path):
    script = write(tmp_path / "w.py", "print('hi')\n")
    monkeypatch.setenv("FAKE_WORKER_SCRIPT", str(script))
    cmd, _, _ = launch.agent_command("fake", "P", "WP01", tmp_path, "b", tmp_path, False)
    assert cmd[0] == sys.executable and cmd[1] == str(script)


# --------------------------------------------------------------------------- #
# cargo-mutants
# --------------------------------------------------------------------------- #

def test_mutants_score():
    assert langs.mutants_score({"caught": 8, "missed": 2, "timeout": 0}) == (0.8, 8, 10)
    assert langs.mutants_score({"caught": 1, "missed": 1, "timeout": 2}, True) == (0.75, 3, 4)
    assert langs.mutants_score({"caught": 1, "missed": 1, "timeout": 2}, False) == (0.25, 1, 4)
    assert langs.mutants_score({"caught": 0, "missed": 0}) == (None, 0, 0)


def test_read_mutants_outcome(tmp_path):
    d = tmp_path / "mutants.out"
    d.mkdir()
    (d / "caught.txt").write_text("a\nb\nc\n", encoding="utf-8")
    (d / "missed.txt").write_text("d\n", encoding="utf-8")
    assert langs.read_mutants_outcome(tmp_path) == {"caught": 3, "missed": 1, "timeout": 0, "unviable": 0}


FAKE_MUTANTS = r'''
import sys, pathlib
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--output") + 1]) / "mutants.out"
out.mkdir(parents=True)
diff = pathlib.Path(args[args.index("--in-diff") + 1]).read_text()
assert "lib.rs" in diff
caught, missed = (int(x) for x in sys.argv[0].split("_")[-1].split(".")[0].split("-"))
(out / "caught.txt").write_text("".join(f"m{i}\n" for i in range(caught)))
(out / "missed.txt").write_text("".join(f"x{i}\n" for i in range(missed)))
sys.exit(2 if missed else 0)
'''


@pytest.mark.parametrize("caught, missed, ok", [(9, 1, True), (7, 3, False)])
def test_run_mutants_threshold(tmp_path, monkeypatch, caught, missed, ok):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo / "src/lib.rs", "pub fn f() -> u8 { 1 }\n")
    base = commit_all(repo, "a")
    write(repo / "src/lib.rs", "pub fn f() -> u8 { 2 }\n")
    head = commit_all(repo, "b")
    script = write(tmp_path / f"fake_mutants_{caught}-{missed}.py", FAKE_MUTANTS)
    exe = sys.executable
    monkeypatch.setenv("AGENTWORK_CARGO_MUTANTS", f'"{exe}" "{script}"' if " " in exe else f'{exe} "{script}"')
    passed, msg = langs.run_mutants(repo, base, head, {"min_kill_ratio": 0.8}, dict(__import__("os").environ))
    assert passed is ok, msg
    assert f"{caught}/{caught + missed}" in msg


def test_run_mutants_no_rust_changes(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo / "README.md", "a\n")
    base = commit_all(repo, "a")
    write(repo / "README.md", "b\n")
    head = commit_all(repo, "b")
    passed, msg = langs.run_mutants(repo, base, head, {}, {})
    assert passed and "no Rust changes" in msg


def test_project_check_commands(monkeypatch):
    monkeypatch.setattr(langs, "has_nextest", lambda: False)
    from awlib import config as c
    cfg = c._deep_merge(c.DEFAULTS, {})
    cmds = dict(langs.project_check_commands(cfg, ["rust"]))
    assert cmds["rust.fmt_command"] == "cargo fmt --all -- --check"
    assert cmds["rust.clippy_command"] == "cargo clippy --all-targets -- -D warnings"
    assert cmds["rust.test_command"] == "cargo test"
    monkeypatch.setattr(langs, "has_nextest", lambda: True)
    assert dict(langs.project_check_commands(cfg, ["rust"]))["rust.test_command"] == "cargo nextest run"
    cfg["verification"]["test_command"] = "make test"
    assert ("test_command", "make test") in langs.project_check_commands(cfg, ["rust"])
