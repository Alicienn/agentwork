"""Launch one coding agent on one work package in a fresh git worktree.

    pipeline.py launch <WP-ID> <agent> [--dry-run] [--retry-note-file F]
    pipeline.py launch --selftest

Portable replacement for launch.sh. Isolation (``project.isolation_mode``):

  auto               best available: Seatbelt (macOS) / bubblewrap (Linux);
                     falls back to worktree-only WITH A LOUD WARNING (Windows).
  worktree-seatbelt  require an OS sandbox; refuse to run without one.
  worktree-only      no OS sandbox; only the git worktree + mechanical gate.

``project.claude_sandbox: true`` additionally enables Claude Code's own sandbox
for the ``claude*`` agents (macOS / Linux / WSL2 only).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import config as awconfig
from .gate import run_gate
from .oscompat import IS_LINUX, IS_MACOS, IS_WINDOWS, WINDOWS_CMDLINE_LIMIT, ProcessTree
from .specs import find_task_spec, tier_of

DEGRADED_BANNER = """
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! WARNING: NO OS SANDBOX — isolation_mode is running as 'worktree-only'.    !!
!! The agent runs with YOUR user rights. It can read and write any file you  !!
!! can (other repos, ~/.ssh, credentials) and use the network.               !!
!! Only the git worktree and the mechanical gate (owns / locked tests /      !!
!! protected files / anti-cheat checks) stand between it and your project.   !!
!! Use macOS or Linux (bwrap), WSL2, or a VM for untrusted agents.           !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""

AGENTS = ("fake", "claude", "claude-or", "claude-local", "codex", "agy", "gemini", "pi-code", "pi-devstral", "pi-fw")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def available_sandbox() -> Optional[str]:
    if IS_MACOS and shutil.which("sandbox-exec"):
        return "seatbelt"
    if IS_LINUX and shutil.which("bwrap"):
        return "bwrap"
    return None


def effective_isolation(mode: str) -> Tuple[str, Optional[str]]:
    """Return (effective mode, sandbox kind). Raise ValueError on impossible config."""
    mode = (mode or "auto").strip()
    if mode not in awconfig.VALID_ISOLATION:
        raise ValueError(f"Unsupported isolation_mode '{mode}'. Supported: {', '.join(awconfig.VALID_ISOLATION)}")
    sb = available_sandbox()
    if mode == "worktree-only":
        return "worktree-only", None
    if mode == "worktree-seatbelt":
        if sb is None:
            raise ValueError("isolation_mode is 'worktree-seatbelt' but neither 'sandbox-exec' (macOS) nor 'bwrap' (Linux) "
                             "is available. Set isolation_mode: auto (degraded, with warning) or worktree-only in project.yaml.")
        return "worktree-seatbelt", sb
    return ("worktree-seatbelt", sb) if sb else ("worktree-only", None)


def check_profile(agent: str, profile: str) -> Optional[str]:
    if profile == "sensitive" and (agent.startswith("claude-or") or agent.startswith("pi-fw") or "openrouter" in agent):
        return f"SECURITY ERROR: Agent '{agent}' is strictly blocked under routing_profile 'sensitive'."
    if profile == "local-only" and agent not in ("claude-local", "pi-code", "pi-devstral", "fake"):
        return f"SECURITY ERROR: Cloud agent '{agent}' is strictly blocked under routing_profile 'local-only'."
    return None


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #

def selftest(mode: str) -> int:
    print(f"=== Running Sandbox Selftest (isolation_mode: {mode}) ===")
    tmp = Path(tempfile.mkdtemp(prefix="sb_selftest_"))
    try:
        (tmp / "allowed.txt").write_text("ok", encoding="utf-8")
        print("PASS: write to permitted temp")
        try:
            eff, sb = effective_isolation(mode)
        except ValueError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        if sb == "seatbelt":
            denied = tmp / "denied"
            denied.mkdir()
            prof = f'(version 1) (allow default) (deny file-write* (subpath "{denied.resolve()}"))'
            r = subprocess.run(["sandbox-exec", "-p", prof, "touch", str(denied / "fail.txt")], capture_output=True)
            if r.returncode == 0:
                print("FAIL: Seatbelt failed to block write to denied directory")
                return 1
            print("PASS: Seatbelt successfully blocked write to denied directory")
        elif sb == "bwrap":
            ro = tmp / "ro"
            ro.mkdir()
            r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                                "touch", str(ro / "fail.txt")], capture_output=True, text=True)
            if r.returncode == 0 and (ro / "fail.txt").exists():
                print("FAIL: bwrap did not make the filesystem read-only")
                return 1
            if "Operation not permitted" in (r.stderr or "") or "setting up uid map" in (r.stderr or ""):
                print(f"FAIL: bwrap cannot create a sandbox here: {r.stderr.strip()}", file=sys.stderr)
                return 1
            print("PASS: Bubblewrap (bwrap) blocked a write outside its writable binds")
        else:
            if mode == "auto":
                print(DEGRADED_BANNER, file=sys.stderr)
                print("PASS (DEGRADED): no OS sandbox available; auto falls back to worktree-only")
            else:
                print("PASS: worktree-only isolation mode selected (no OS sandbox)")
        print("=== Selftest complete ===")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

def emit_prompt(repo: Path, pkg: Path, wp_id: str, wt: Path, branch: str, retry_note: Optional[str]) -> str:
    content = pkg.read_text(encoding="utf-8")
    tier = tier_of(content)
    brief = repo / "agentwork" / "CONTEXT.md"
    lite = repo / "agentwork" / "CONTEXT-lite.md"
    if tier == "mini-code" and lite.exists():
        brief = lite
    parts = [brief.read_text(encoding="utf-8") if brief.exists() else "", "\n\n---\n\n", content]
    try:
        res = subprocess.run([sys.executable, str(repo / "agentwork" / "pipeline.py"), "context", wp_id, "--symbols-only"],
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        sym = res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        sym = ""
    if sym:
        parts.append(f"\n\n---\n# AUTO-EXTRACTED INTERFACE & DEPENDENCY SIGNATURES\n\n{sym}\n")
    if retry_note and Path(retry_note).is_file():
        parts.append("\n\n---\n# PREVIOUS ATTEMPT FAILURE / REPAIR REQUIRED\n\n")
        parts.append(Path(retry_note).read_text(encoding="utf-8", errors="replace"))
    parts.append(f"\n\n---\nExecution: you are working in worktree {wt} on branch {branch}.\n")
    parts.append(f"Touch ONLY the files listed in Owns / owns:, commit locally, never push, and write "
                 f"agentwork/reports/{wp_id.lower()}.md when done.\n")
    parts.append("If this package is blocked by an unstated requirement ambiguity, write a ## Question section in your report.\n")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Worktree
# --------------------------------------------------------------------------- #

def _git(*args: str, cwd: Path, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=quiet, text=True, check=check)


def trees_dir(repo: Path) -> Path:
    env = os.environ.get("AGENT_TREES")
    return Path(env).resolve() if env else repo.parent / f"{repo.name}-agent-trees"


def recreate_worktree(repo: Path, wt: Path, branch: str, base_sha: str) -> None:
    wt.parent.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        if r.returncode != 0:
            shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
    if subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
    _git("worktree", "add", "-b", branch, str(wt), base_sha, cwd=repo, quiet=True)


def seed_files(repo: Path, pkg: Path, wt: Path, wp_id: str, env: Dict[str, str]) -> Optional[str]:
    seed = pkg.with_suffix(".files")
    if not seed.is_dir():
        return None
    for src in sorted(seed.rglob("*")):
        if src.is_file():
            dst = wt / src.relative_to(seed)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    if subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", f"chore(seed): pre-seed files for {wp_id}"],
                       check=True, env=env)
        return subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return None


# --------------------------------------------------------------------------- #
# Agent command lines
# --------------------------------------------------------------------------- #

def _need(binary: str) -> str:
    p = shutil.which(binary)
    if not p:
        raise FileNotFoundError(f"'{binary}' not found in PATH")
    return p


def agent_command(agent: str, prompt: str, wp_id: str, wt: Path, branch: str, attempt_dir: Path,
                  claude_sandbox: bool) -> Tuple[List[str], Dict[str, Optional[str]], Optional[str]]:
    """Return (argv, env changes, stdin text)."""
    env: Dict[str, Optional[str]] = {}
    stdin: Optional[str] = None
    sandbox_settings = None
    if claude_sandbox and agent.startswith("claude"):
        sandbox_settings = attempt_dir / "claude-sandbox-settings.json"
        sandbox_settings.write_text(json.dumps({"sandbox": {"enabled": True}}), encoding="utf-8")

    if agent == "fake":
        script = os.environ.get("FAKE_WORKER_SCRIPT")
        if script and Path(script).is_file():
            if script.endswith(".py"):
                return [sys.executable, script, wp_id, str(wt), branch], env, None
            return [_need("bash"), script, wp_id, str(wt), branch], env, None
        return [sys.executable, "-c", "import subprocess; print('fake worker pass'); subprocess.run(['git','status'])"], env, None

    if agent == "claude":
        env.update({"ANTHROPIC_BASE_URL": None, "ANTHROPIC_AUTH_TOKEN": None, "ANTHROPIC_API_KEY": None})
        cmd = [_need("claude"), "-p", "--permission-mode", "bypassPermissions"]
        if sandbox_settings:
            cmd += ["--settings", str(sandbox_settings)]
        return cmd, env, prompt  # prompt on stdin: no command-line length limit, no quoting issues

    if agent.startswith("claude-or"):
        alias = agent.split(":", 1)[1] if ":" in agent else ""
        if shutil.which("claude-or"):
            cmd = [_need("claude-or")] + ([alias] if alias else []) + ["--permission-mode", "bypassPermissions", prompt]
            return cmd, env, None
        if shutil.which("claude") and os.environ.get("OPENROUTER_API_KEY"):
            env.update({"ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                        "ANTHROPIC_AUTH_TOKEN": os.environ["OPENROUTER_API_KEY"]})
            model = os.environ.get("OPENROUTER_MODEL", "nvidia/llama-3.1-nemotron-ultra-253b-v1:free")
            cmd = [_need("claude"), "-p", "--model", model, "--permission-mode", "bypassPermissions"]
            if sandbox_settings:
                cmd += ["--settings", str(sandbox_settings)]
            return cmd, env, prompt
        raise FileNotFoundError("'claude-or' wrapper not found and OPENROUTER_API_KEY not set.")

    if agent == "claude-local":
        if shutil.which("claude-local"):
            return [_need("claude-local"), "-p", "--permission-mode", "bypassPermissions"], env, prompt
        if shutil.which("claude"):
            env.update({"ANTHROPIC_BASE_URL": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                        "ANTHROPIC_API_KEY": "ollama"})
            cmd = [_need("claude"), "-p", "--model", os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:32b"),
                   "--permission-mode", "bypassPermissions"]
            if sandbox_settings:
                cmd += ["--settings", str(sandbox_settings)]
            return cmd, env, prompt
        raise FileNotFoundError("'claude-local' or 'claude' CLI not found.")

    if agent == "codex":
        return [_need("codex"), "exec", "--dangerously-bypass-approvals-and-sandbox", "--ephemeral", prompt], env, None
    if agent == "agy":
        return [_need("agy"), "--dangerously-skip-permissions", "-p", "--print-timeout",
                os.environ.get("PRINT_TIMEOUT", "180m"), prompt], env, None
    if agent == "gemini":
        return [_need("gemini"), "-y", "-p", prompt], env, None
    if agent in ("pi-code", "pi-devstral"):
        return [_need("pi"), "--provider", os.environ.get("PI_PROVIDER", "ollama"), "--model", agent, "--thinking", "off",
                "--tools", "read,bash,edit,write,subagent,grep,find,ls", "-p", prompt], env, None
    if agent == "pi-fw":
        return [_need("pi"), "--provider", "fireworks", "--model", os.environ.get("PI_FW_MODEL", "kimi-k3"),
                "--tools", "read,bash,edit,write,subagent,grep,find,ls", "-p", prompt], env, None
    raise ValueError(f"Unknown agent '{agent}'")


def shrink_prompt_for_windows(cmd: List[str], prompt: str, prompt_file: Path) -> List[str]:
    """Windows limits command lines to ~32k chars and .cmd shims re-parse arguments.

    When the prompt is passed as an argument and either problem applies, replace
    it with a short pointer to the prompt file.
    """
    if not IS_WINDOWS or prompt not in cmd:
        return cmd
    is_shim = cmd[0].lower().endswith((".cmd", ".bat"))
    if len(subprocess.list2cmdline(cmd)) < WINDOWS_CMDLINE_LIMIT and not is_shim:
        return cmd
    pointer = (f"Your full instructions are in the file {prompt_file}. Read that whole file first and follow it exactly.")
    return [pointer if c == prompt else c for c in cmd]


def wrap_in_sandbox(sb: str, cmd: List[str], wt: Path, common_git: Path, wp_id: str, attempt_dir: Path) -> List[str]:
    real_wt = wt.resolve()
    real_git = common_git.resolve()
    home = Path.home()
    if sb == "seatbelt":
        branch_lower = wp_id.lower()
        profile = f"""(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath "{real_wt}")
  (subpath "{real_git}/worktrees/{wp_id}")
  (subpath "{real_git}/objects")
  (subpath "{attempt_dir.resolve()}")
  (subpath "/private/tmp")
  (literal "/dev/null")
  (regex #"^/dev/tty")
  (regex #"^{real_git}/(logs/)?refs/heads/wp/{branch_lower}(\\.lock)?$"))
(deny file-read*
  (subpath "{home}/.ssh")
  (subpath "{home}/.aws")
  (subpath "{home}/.config/gh")
  (subpath "{home}/.local/state/agentwork"))"""
        return ["sandbox-exec", "-p", profile] + cmd
    if sb == "bwrap":
        for sub in (f"worktrees/{wp_id}", "objects", "refs/heads/wp", "logs/refs/heads/wp"):
            (real_git / sub).mkdir(parents=True, exist_ok=True)
        args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--bind", str(real_wt), str(real_wt),
                "--bind", str(real_git / "worktrees" / wp_id), str(real_git / "worktrees" / wp_id),
                "--bind", str(real_git / "objects"), str(real_git / "objects"),
                "--bind", str(real_git / "refs/heads/wp"), str(real_git / "refs/heads/wp"),
                "--bind", str(real_git / "logs/refs/heads/wp"), str(real_git / "logs/refs/heads/wp"),
                "--bind", str(attempt_dir.resolve()), str(attempt_dir.resolve()),
                "--bind", "/tmp", "/tmp"]
        for secret in (".ssh", ".aws", ".config/gh", ".local/state/agentwork"):
            if (home / secret).exists():
                args += ["--tmpfs", str(home / secret)]
        args += ["--chdir", str(real_wt)]
        return args + cmd
    return cmd


def run_tee(cmd: List[str], cwd: Path, env: Dict[str, str], log_file: Path, stdin_text: Optional[str]) -> int:
    with open(log_file, "wb") as log:
        tree = ProcessTree(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL)
        proc = tree.proc
        if stdin_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass
        assert proc.stdout is not None
        out = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else None
        try:
            for chunk in iter(lambda: proc.stdout.readline(), b""):
                log.write(chunk)
                if out is not None:
                    out.write(chunk)
                    out.flush()
            return tree.wait()
        except KeyboardInterrupt:
            tree.kill_tree()
            raise


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def launch(wp_id: str, agent: str, dry_run: bool = False, retry_note_file: Optional[str] = None) -> int:
    repo = repo_root()
    cfg = awconfig.load_project_config(repo / "agentwork" / "config" / "project.yaml")
    profile = str(cfg["project"].get("routing_profile") or "openrouter-first")
    mode = str(cfg["project"].get("isolation_mode") or "auto")
    claude_sandbox = bool(cfg["project"].get("claude_sandbox", False))

    err = check_profile(agent, profile)
    if err:
        print(err, file=sys.stderr)
        return 2

    wp_id = wp_id.upper()
    pkg = find_task_spec(wp_id, [repo / "agentwork" / "packages", repo / "agentwork" / "steps"])
    if pkg is None:
        print(f"ERROR: no spec found for task ID {wp_id}", file=sys.stderr)
        return 1

    branch = f"wp/{wp_id.lower()}"
    wt = trees_dir(repo) / wp_id
    if dry_run:
        print(f"package: {pkg.stem} | agent: {agent} | tier: {tier_of(pkg.read_text(encoding='utf-8'))} | "
              f"profile: {profile} | isolation: {mode} | (dry run)")
        print("\n".join(emit_prompt(repo, pkg, wp_id, wt, branch, retry_note_file).splitlines()[:40]))
        return 0

    try:
        eff_mode, sb = effective_isolation(mode)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    sandbox_fake = os.environ.get("AGENTWORK_SANDBOX_FAKE") == "1"
    if agent == "fake" and not sandbox_fake:
        sb = None
    if eff_mode == "worktree-only" and agent != "fake":
        print(DEGRADED_BANNER, file=sys.stderr, flush=True)
    if claude_sandbox and agent.startswith("claude") and IS_WINDOWS:
        print("WARNING: claude_sandbox is enabled but Claude Code's sandbox is not supported on native Windows "
              "(use WSL2). The agent will run unsandboxed.", file=sys.stderr)

    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    common_git = Path(common) if common else repo / ".git"
    base_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    recreate_worktree(repo, wt, branch, base_sha)
    print(f"worktree: created fresh {wt} on {branch} (base {base_sha})", flush=True)

    git_env = dict(os.environ)
    git_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "agentwork", "GIT_AUTHOR_EMAIL": "agentwork@local",
                    "GIT_COMMITTER_NAME": "agentwork", "GIT_COMMITTER_EMAIL": "agentwork@local"})
    seeded = seed_files(repo, pkg, wt, wp_id, git_env)
    if seeded:
        base_sha = seeded
        print(f"seeded and committed baseline files from {pkg.with_suffix('.files')} (new gate base: {base_sha})")

    log_dir = repo / "agentwork" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    attempt_uuid = os.environ.get("ATTEMPT_UUID") or uuid.uuid4().hex
    safe_agent = agent.replace(":", "_")
    prompt_file = log_dir / f"{wp_id}-{safe_agent}-{stamp}-{attempt_uuid[:6]}-prompt.md"
    log_file = log_dir / f"{wp_id}-{safe_agent}-{stamp}-{attempt_uuid[:6]}.log"
    prompt = emit_prompt(repo, pkg, wp_id, wt, branch, retry_note_file)
    prompt_file.write_text(prompt, encoding="utf-8")

    attempt_dir = Path(tempfile.gettempdir()) / "agentwork-attempts" / attempt_uuid
    for sub in ("codex", "tmp", "uv-cache"):
        (attempt_dir / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(attempt_dir, 0o700)
    except OSError:
        pass
    rc = 1
    try:
        for cand in (Path.home() / ".codex" / "auth.json", Path.home() / ".codex-cplt" / "auth.json"):
            if cand.is_file():
                try:
                    shutil.copyfile(cand, attempt_dir / "codex" / "auth.json")
                except OSError:
                    pass
                break
        run_env = dict(git_env)
        run_env.update({"TMPDIR": str(attempt_dir / "tmp"), "UV_CACHE_DIR": str(attempt_dir / "uv-cache"),
                        "CODEX_HOME": str(attempt_dir / "codex")})
        if IS_WINDOWS:
            run_env.update({"TEMP": str(attempt_dir / "tmp"), "TMP": str(attempt_dir / "tmp")})

        _prepare_worktree(wt, run_env)

        try:
            cmd, env_changes, stdin_text = agent_command(agent, prompt, wp_id, wt, branch, attempt_dir, claude_sandbox)
        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        for k, v in env_changes.items():
            if v is None:
                run_env.pop(k, None)
            else:
                run_env[k] = v
        cmd = shrink_prompt_for_windows(cmd, prompt, prompt_file)
        if sb:
            cmd = wrap_in_sandbox(sb, cmd, wt, common_git, wp_id, attempt_dir)
        print(f"--- launching {agent} on {wp_id} in {wt} (isolation: {eff_mode}{'/' + sb if sb else ''}) ---", flush=True)
        try:
            rc = run_tee(cmd, wt, run_env, log_file, stdin_text)
        except FileNotFoundError as e:
            print(f"ERROR: cannot start agent: {e}", file=sys.stderr)
            rc = 127
        print(f"--- {agent} exited with code {rc} ---", flush=True)
    finally:
        shutil.rmtree(attempt_dir, ignore_errors=True)

    print(f"--- running mechanical gate for {wp_id} ---", flush=True)
    gate_rc = run_gate(wp_id, str(wt), base_sha)
    if gate_rc != 0:
        print(f"GATE: Mechanical gate FAILED (exit {gate_rc}).")
        return gate_rc
    print("GATE: Mechanical gate PASSED.")
    return rc


def _prepare_worktree(wt: Path, env: Dict[str, str]) -> None:
    scripts = wt / "agentwork" / "scripts"
    for name, runner in (("prepare-worktree.py", [sys.executable]),
                         ("prepare-worktree.sh", [shutil.which("bash") or "bash"]),
                         ("prepare-worktree.cmd", ["cmd", "/c"] if IS_WINDOWS else None)):
        p = scripts / name
        if runner is None or not p.is_file():
            continue
        if name.endswith(".sh") and not IS_WINDOWS and not os.access(p, os.X_OK):
            continue
        r = subprocess.run(runner + [str(p)], cwd=wt, env=env)
        if r.returncode != 0:
            print("worktree prep failed (continuing)")
        return


def cli_main(argv: Sequence[str]) -> int:
    args = list(argv)
    if args and args[0] == "--selftest":
        repo = repo_root()
        cfg = awconfig.load_project_config(repo / "agentwork" / "config" / "project.yaml")
        return selftest(str(cfg["project"].get("isolation_mode") or "auto"))
    ap = argparse.ArgumentParser(prog="pipeline.py launch")
    ap.add_argument("wp_id")
    ap.add_argument("agent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stream", action="store_true", help="(accepted for compatibility)")
    ap.add_argument("--no-stream", action="store_true", help="(accepted for compatibility)")
    ap.add_argument("--retry-note-file")
    ns = ap.parse_args(args)
    return launch(ns.wp_id, ns.agent, ns.dry_run, ns.retry_note_file)
