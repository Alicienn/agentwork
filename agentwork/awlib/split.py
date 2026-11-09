"""Split one oversized work package into smaller ones (portable split.sh).

    pipeline.py split <WP-ID> [claude|gemini|codex] [--target pi-code|hosted] [--tier T] [--dry-run]

Delegates the decomposition to a capable agent — a judgement call, not a text
operation. Writes NEW packages (WP<NN>a, WP<NN>b, ...) and leaves the original.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from . import config as awconfig
from .launch import repo_root, run_tee, shrink_prompt_for_windows, trees_dir
from .specs import _is_skipped

PI_CODE_TEXT = """# These packages will be implemented by a LOCAL 30B model

It has no internet, a modest context window, and it fails on open design
decisions rather than on volume. Splitting a big package into three equally open
ones achieves nothing. Each package you write must be *closed*: everything to
decide is already decided.

So, for every package you produce:

- Fix the interfaces — data shapes, function signatures, field names, return
  types. The implementer implements; it does not design.
- **Ship a stub and the tests as files, not as code blocks in the spec.** Create
  `agentwork/packages/<pkg-name>.files/` mirroring repo paths, containing:
    * the target source file as a stub — real signatures and docstrings, every
      body `raise NotImplementedError` (Python) or `todo!()` (Rust);
    * the test file that checks accepted behavior (not new design authority).
      For Rust, locked acceptance tests go in `tests/` (integration tests), never
      in a `#[cfg(test)] mod tests` inside `src/`.
  The harness copies these into the worktree before the run. The spec then says
  "these are already in your worktree; implement the bodies, do not edit the
  tests". **Never paste code into the spec inside a fenced block for the agent
  to write out** — small models copy the fence markers into the file verbatim
  and break it.
- Give each package one or two owned files, disjoint from every sibling.
- Leave nothing to invent: no "choose a suitable X", no "design a scheme for Y".
  Resolve only engineering choices already within delegated bounds and record
  them. If closure needs a semantic decision, report a blocker instead.
- Add a `Verify:` line near the top: one shell command, run by the harness
  after the agent exits, that exits non-zero on failure. Its result decides
  whether the package is done.
"""

HOSTED_TEXT = """# These packages will be implemented by a HOSTED agent (claude/codex/gemini)

Packages may be larger and leave engineering choices FREE within explicit bounds.
State accepted semantics, constraints, and invariants; do not leave required
product semantics OPEN. Plan-tier exploration may propose unaccepted alternatives,
but those proposals do not authorize implementation.

- No stub files are needed; do not create a `.files/` directory.
- Describe the deliverables and the acceptance criteria rather than fixing every
  signature.
- Still give each package a disjoint set of owned files — that is what makes
  parallel runs safe.
- Add a `Verify:` line near the top where a mechanical check exists: one shell
  command, run by the harness after the agent exits.
"""


def build_prompt(repo: Path, pkg: Path, wp_id: str, target: str, tier: str, wt: Path, branch: str) -> str:
    pkg_name = pkg.stem
    parts: List[str] = ["""You are decomposing one work package into several smaller ones for a
parallel-agents workflow. You are NOT writing implementation code.

Preserve the parent's accepted design revision, node IDs, constraints, and FREE
bounds in every child. Unspecified behavior is not permission to invent. Do not
resolve OPEN, PROVISIONAL, or DEFERRED semantics to make a package executable.
Report such blockers for human steering; emit executable children only for
independent authorized scope. Trace new observable test expectations to accepted
intent/design. Include the relevant contract excerpt even with older seeded briefs.

"""]
    parts.append(PI_CODE_TEXT if target == "pi-code" else HOSTED_TEXT)
    parts.append(f"""
Order the packages so that any shared type or interface is created by the FIRST
package, and later ones consume it. Use a soft dependency only with an already
authorized fallback; otherwise mark it blocking and order execution accordingly.

# The tier line

Every package you write starts with `Tier: {tier}` on its own line, directly
under the title.

# Your task

Split **{pkg_name}** into the smallest number of `Tier: {tier}` packages that
meet the target's constraints above — prefer 2–4. Name them {wp_id}a-<slug>.md,
{wp_id}b-<slug>.md, … in `agentwork/packages/`.

Do NOT modify or delete {pkg_name} — it stays, so it can still be run whole on a
capable agent.

Also write `agentwork/packages/{wp_id}-SPLIT.md`: which package does what,
the order to run them in, interfaces fixed by accepted authority, delegated
engineering choices, and unresolved blockers requiring human steering.

Follow the shape of `agentwork/packages/WP-template.md`. Commit locally.
Never push. Do not touch source or test files outside the package specs.

---

# The package being split

""")
    parts.append(pkg.read_text(encoding="utf-8"))
    parts.append("\n\n---\n\n# The shared brief these packages are written against\n\n")
    ctx = repo / "agentwork" / "CONTEXT.md"
    parts.append(ctx.read_text(encoding="utf-8") if ctx.exists() else "")
    lite = repo / "agentwork" / "CONTEXT-lite.md"
    if tier == "mini-code" and lite.exists():
        parts.append("\n\n---\n\n# The lite brief a Tier: mini-code package will actually receive\n\n")
        parts.append("Anything such a package needs that is NOT in here must be stated in the\n")
        parts.append("package spec itself, because the agent will never see the full brief.\n\n")
        parts.append(lite.read_text(encoding="utf-8"))
    parts.append(f"\n\n---\nExecution: you are in worktree {wt} on branch {branch}. Work only here.\n")
    return "".join(parts)


def cli_main(argv: Sequence[str]) -> int:
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 1
    wp_id = args.pop(0).upper()
    agent, tier, target, dry = "claude", "", "pi-code", False
    while args:
        a = args.pop(0)
        if a in ("claude", "gemini", "codex"):
            agent = a
        elif a in ("claude-local", "pi-mini", "pi-code", "pi-local"):
            print(f"ERROR: splitting needs a capable agent; not {a}", file=sys.stderr)
            return 1
        elif a == "--target" and args:
            target = args.pop(0)
        elif a == "--tier" and args:
            tier = args.pop(0)
        elif a == "--dry-run":
            dry = True
        else:
            print(f"ERROR: unknown option {a}", file=sys.stderr)
            return 1
    if target not in ("pi-code", "hosted"):
        print("ERROR: --target must be pi-code or hosted", file=sys.stderr)
        return 1
    tier = tier or "code"

    repo = repo_root()
    cfg = awconfig.load_project_config(repo / "agentwork" / "config" / "project.yaml")
    if str(cfg["project"].get("routing_profile")) == "local-only":
        print(f"SECURITY ERROR: split requires a hosted reasoning agent ({agent}), which is blocked under "
              f"routing_profile 'local-only'.", file=sys.stderr)
        return 2

    pkg: Optional[Path] = None
    pdir = repo / "agentwork" / "packages"
    for f in sorted(pdir.glob("*.md")) if pdir.is_dir() else []:
        if _is_skipped(f.name):
            continue
        if f.name.split("-", 1)[0].upper() == wp_id:
            pkg = f
            break
    if pkg is None:
        print(f"ERROR: no agentwork/packages/{wp_id}-*.md", file=sys.stderr)
        return 1

    base = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip() or "main"
    branch = f"split/{wp_id.lower()}"
    wt = trees_dir(repo) / f"SPLIT-{wp_id}"
    prompt = build_prompt(repo, pkg, wp_id, target, tier, wt, branch)

    if dry:
        print(f"package: {pkg.stem} | agent: {agent} | target: {target} | tier: {tier} | (dry run — nothing created)")
        print("--- assembled prompt (preview) ---")
        print("\n".join(prompt.splitlines()[:45]))
        return 0

    wt.parent.mkdir(parents=True, exist_ok=True)
    if not wt.exists():
        exists = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
        cmd = ["git", "-C", str(repo), "worktree", "add"] + ([str(wt), branch] if exists else ["-b", branch, str(wt), base])
        subprocess.run(cmd, check=True)
        print(f"worktree: {wt} on {branch} (base {base})")
    else:
        print(f"worktree: reusing {wt}")

    log_dir = repo / "agentwork" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prompt_file = log_dir / f"SPLIT-{wp_id}-{agent}-{stamp}-prompt.md"
    log_file = log_dir / f"SPLIT-{wp_id}-{agent}-{stamp}.log"
    prompt_file.write_text(prompt, encoding="utf-8")
    print(f"package: {pkg.stem} | agent: {agent} | target: {target} | tier: {tier} | prompt: {prompt_file}")

    env = dict(os.environ)
    stdin_text = None
    if agent == "claude":
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            env.pop(k, None)
        cmd = [shutil.which("claude") or "claude", "-p"]
        if os.environ.get("CLAUDE_MODEL"):
            cmd += ["--model", os.environ["CLAUDE_MODEL"]]
        cmd += (os.environ.get("CLAUDE_FLAGS") or "--permission-mode bypassPermissions").split()
        stdin_text = prompt
    elif agent == "gemini":
        cmd = [shutil.which("agy") or "agy"]
        if os.environ.get("GEMINI_MODEL"):
            cmd += ["--model", os.environ["GEMINI_MODEL"]]
        cmd += (os.environ.get("GEMINI_FLAGS") or "--dangerously-skip-permissions").split()
        cmd += ["--print-timeout", os.environ.get("GEMINI_PRINT_TIMEOUT", "180m"), "-p", prompt]
    else:
        cmd = [shutil.which("codex") or "codex", "exec"]
        if os.environ.get("CODEX_MODEL"):
            cmd += ["-m", os.environ["CODEX_MODEL"]]
        git_dir = (repo / ".git").as_posix()
        cmd += ["-c", f'sandbox_workspace_write.writable_roots=["{git_dir}"]']
        cmd += (os.environ.get("CODEX_FLAGS") or "--full-auto").split()
        cmd += [prompt]
    cmd = shrink_prompt_for_windows(cmd, prompt, prompt_file)
    print(f"--- splitting {wp_id} with {agent} in {wt} ---")
    try:
        rc = run_tee(cmd, wt, env, log_file, stdin_text)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        rc = 127
    print(f"--- {agent} exited {rc} ---")
    st = subprocess.run(["git", "-C", str(wt), "status", "--short"], capture_output=True, text=True).stdout
    print("\n".join(st.splitlines()[:20]))
    print("--- packages produced ---")
    produced = []
    wpk = wt / "agentwork" / "packages"
    if wpk.is_dir():
        for f in sorted(wpk.glob("*.md")):
            low = f.name.lower()
            if low == f"{wp_id.lower()}-split.md" or (low.startswith(wp_id.lower()) and len(low) > len(wp_id) + 1
                                                     and low[len(wp_id)].isalpha() and low[len(wp_id) + 1] == "-"):
                produced.append(f.name)
    print("\n".join(f"  {p}" for p in produced) if produced else "  none — re-run to resume")
    print("\nReview the specs, then merge the branch and launch them:")
    print(f"  git merge --no-ff {branch}")
    print(f"  python agentwork/pipeline.py launch {wp_id}a claude-local")
    return rc
