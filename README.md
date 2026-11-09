# Unified Agent Template (Exploration + Autonomous Pipeline)

> **This is a fork of [apelov/agentwork](https://github.com/apelov/agentwork)** (MIT, © apelov — see `LICENSE`).
> The `rust-crossplatform` branch adds **Rust** support and makes the harness run on **Windows, Linux and macOS**.
> See [What this fork changes](#what-this-fork-changes) below.

A portable, deterministic multi-agent engineering workflow for AI coding agents (`claude`, `claude-or`, `codex`, `agy`, `claude-local`, `pi-code`). Supports both **interactive epistemic design exploration** (`.ai/workflows/design-exploration.md`) and **fully autonomous multi-agent execution** (`agentwork/pipeline.py drive`).

## Core Modes

1. **Interactive / Exploration Mode (Default)**:
   - Reads `AGENTS.md` and `.ai/session.md`.
   - Uses `.ai/workflows/design-exploration.md` to map `AXIOM`, `CHOICE`, `ASSUMPTION`, and `QUESTION` nodes (`FIXED [F]`, `FREE [*]`, `OPEN [?]`, `DEFERRED [D]`, `REJECTED [X]`) and let the human steer before freezing `docs/design/DESIGN.md`.
   - Also supports `.ai/workflows/big-work.md` (IETF drafts, research, long-form writing) and `.ai/workflows/plan-then-execute.md`.
2. **Autonomous Pipeline Mode (Phases 1–4)**:
   - **Interchangeable Driver**: Phases 1, 2, and 4 can be driven by Claude Code, Codex, or Antigravity (`agy`) following `Bootstrap an Autonomous Multi-Agent Repository.md`.
   - **Controller-First Execution**: Phase 3 (Build) is executed deterministically by `python3 agentwork/pipeline.py drive`, which manages worktrees, gates, retries, and integration.
   - **Privacy Profiles**:
     - `openrouter-first`: Leans on free models (`claude-or:nemotron_ultra`, `nemotron_super`) and local models, with paid fallback on quota/failure.
     - `sensitive`: Hard-blocks `claude-or` and third-party routers; allows only approved direct subscription endpoints (`claude`, `codex`, `agy`) and local models.
     - `balanced`: Multi-vendor subscription CLIs and local models.
     - `local-only`: Airgapped local Ollama only.
   - **Worktree-Aware Isolation**: Workers run in dedicated git worktrees (`../<repo>-agent-trees/<ID>`) with narrowed file-write permissions enforced by Darwin Seatbelt (macOS) or bubblewrap (Linux), plus the Layer-1 mechanical gate (`pipeline.py gate`, formerly `verify_gate.sh`).

## Quickstart

Requirements: **Python 3.9+** and **git** (plus `cargo`, `rustfmt`, `clippy` for Rust projects). No Python packages are needed to run the harness.

### 1. Install or Update a Project

Linux / macOS:
```bash
./setup.sh /path/to/project --profile openrouter-first   # or --profile sensitive | balanced | local-only
./setup.sh /path/to/project --dry-run                    # preview updates without writing
```

Windows (cmd or PowerShell):
```bat
setup.cmd C:\path\to\project --profile balanced
```

Every launcher just calls `python agentwork/pipeline.py <subcommand>`, so this also works everywhere:
```bash
python agentwork/pipeline.py setup /path/to/project --profile balanced
```

### 2. Run Interactive / Design Exploration
> Read `AGENTS.md`, then load `.ai/session.md` and propose the next step.

### 3. Run Autonomous Multi-Agent Build
> Implement `<Project Goal>` following `Bootstrap an Autonomous Multi-Agent Repository.md`

Or run the headless autopilot directly once sealed:
```bash
cd /path/to/project
./agentwork/scripts/driver.sh --build-all        # Windows: agentwork\scripts\driver.cmd --build-all
```

## What this fork changes

### 1. Runs on Windows, Linux and macOS

All logic that lived in bash is now portable Python in `agentwork/awlib/` (no dependencies). The old script names are kept as thin launchers:

| Old bash script | Now | Launchers |
|---|---|---|
| `agentwork/scripts/verify_gate.sh` | `pipeline.py gate` (`awlib/gate.py`) | `verify_gate.sh` / `verify_gate.cmd` |
| `agentwork/scripts/launch.sh` | `pipeline.py launch` (`awlib/launch.py`) | `launch.sh` / `launch.cmd` |
| `agentwork/scripts/split.sh` | `pipeline.py split` (`awlib/split.py`) | `split.sh` / `split.cmd` |
| `agentwork/scripts/driver.sh` | `pipeline.py driver` (`awlib/driver.py`) | `driver.sh` / `driver.cmd` |
| `setup.sh` | `pipeline.py setup` (`awlib/setup_cmd.py`) | `setup.sh` / `setup.cmd` |
| `update-all.sh` | `pipeline.py update-all` | `update-all.sh` / `update-all.cmd` |

Portability fixes:
- **Interpreter**: launchers pick `python3`/`python` (POSIX) or `py -3`/`python`/`python3` (Windows), checking for 3.9+; override with `PYTHON_BIN`. Inside the pipeline every sub-step re-uses `sys.executable`.
- **Timeouts kill the whole process tree**: POSIX sessions + `killpg`; on Windows the agent is started suspended inside a kill-on-close **Job Object**, so even grandchildren that outlive their parent are killed (fallback: `taskkill /T /F`).
- **Locks**: `fcntl` on POSIX, `msvcrt` on Windows (state lock, `drive` lock).
- **Paths**: `pathlib` everywhere; paths stored in the manifest and compared with globs are POSIX-style; `git diff -z --no-renames`.
- **Line endings**: `.gitattributes` keeps LF (CRLF only for `.cmd`), and the seal hashes text with CRLF→LF normalisation, so a seal made on Linux verifies on a Windows checkout with `core.autocrlf=true`.
- **Shell for `Verify:` / `done_when:` commands**: bash or sh on POSIX, `cmd.exe` on Windows (`verification.shell` in `project.yaml` can force `bash`, `pwsh`, ...). Write commands that work in both (`cargo test`, `python -m pytest -q`).
- **Long prompts on Windows**: `claude` agents receive the prompt on stdin; for other agents a prompt that would exceed the ~32k command-line limit (or go through a `.cmd` shim) is replaced by a pointer to the prompt file.
- No more `sed`, `grep`, `awk`, `uuidgen`, `shasum`, `ln -s` or `chmod` in the critical path.

### Windows: degraded isolation

**Windows has no equivalent of Seatbelt or bubblewrap that the harness can use, so on Windows agents run without an OS sandbox.**

- `isolation_mode: auto` (the new default) uses Seatbelt on macOS and bubblewrap on Linux, and **falls back to `worktree-only` on Windows with a large warning printed at every launch** (`launch --selftest` and `doctor` report `DEGRADED`).
- In `worktree-only` mode the agent runs with **your user rights**: it can read and write any file you can (other repos, `~/.ssh`, browser profiles, credentials) and use the network. What still protects the project is the git worktree plus the **mechanical gate** (owns, locked tests, protected files, anti-cheat checks, seal) — the gate decides what gets merged, but it cannot undo what the agent did elsewhere on your machine.
- `isolation_mode: worktree-seatbelt` keeps the original strict behaviour: it refuses to start an agent when no sandbox exists (so it always refuses on Windows).
- For untrusted agents on Windows, run the harness inside **WSL2** (then bubblewrap works) or a VM / dev container.
- `claude_sandbox: true` additionally turns on Claude Code's own sandbox for `claude*` agents (`--settings` with `sandbox.enabled`). Claude Code documents this sandbox for macOS, Linux and WSL2 only; on native Windows it has no effect and the harness says so.

### 2. Rust support (Python support kept)

- **Languages are a setting**: `languages: auto` detects `Cargo.toml` (Rust) and `pyproject.toml`/`setup.py`/`requirements.txt` (Python); or list them explicitly (`languages: [python, rust]`). Remove one to switch its checks off.
- **Default Rust commands** (FINAL gate, and per task with `verification.run_project_checks_per_task: true`): `cargo nextest run` if cargo-nextest is installed, else `cargo test`; `cargo fmt --all -- --check`; `cargo clippy --all-targets -- -D warnings`. Override them under `rust:` or with `verification.test_command` / `lint_command`.
- **Anti-cheat checks for Rust**, on the `.rs` files changed by the package, with a real parser — `agentwork/tools/rust-testcheck` (built on `syn`, compiled once into the user cache), with a pure-Python lexer/parser fallback (`awlib/rustsrc.py`) when it cannot be built (`rust.checker: auto | syn | builtin`). The gate rejects (exit 8):
  - `#[ignore]` and `#[cfg_attr(..., ignore)]` on a test;
  - a `#[test]` (also `#[tokio::test]`, `#[rstest]`, `#[test_case]`, ...) with an empty body or without any assertion (`assert*!`, `panic!`, a call to `assert*()`, `#[should_panic]`, or names listed in `rust.extra_assertion_names`);
  - `todo!()` / `unimplemented!()` in test code (test functions, `#[cfg(test)]` modules, files under `tests/`);
  - tests or test modules disabled by an always-false cfg: `#[cfg(any())]`, `#[cfg(not(test))]`, `#[cfg(all(x, any()))]`, `#![cfg(any())]` on a whole test file, ...;
  - new crate/module-level `#![allow(...)]` / `#![expect(...)]` and blanket `#[allow(warnings | clippy::all | ...)]`;
  - removed or renamed tests (`verification.forbid_test_removal`, also for Python);
  - unparsable Rust files.

  Only *new* problems count: each file is analysed at the base commit and at the candidate, so old code is not blamed.
- **Locked tests can no longer be bypassed by deletion or renaming** (`git diff --no-renames`).
- **Guarded build/lint configuration** (exit 5): `Cargo.toml`, `Cargo.lock`, `build.rs`, `clippy.toml`, `rustfmt.toml`, `.cargo/**`, `rust-toolchain*`, `deny.toml` (list in `rust.guarded_files`) may change only when the exact path is listed in the package's `owns:` — a glob such as `**` does not count.
- **`pipeline.py context`** extracts Rust signatures (`pub fn`, `pub struct` with public fields, `pub enum` with variants, `pub trait` with its methods, `pub type`/`const`, and `impl` blocks with their public methods), and follows `mod foo;` / `use crate::...` one hop.
- **Optional `cargo-mutants` check** (off by default): with `rust.mutants.enabled: true`, every package gate runs `cargo mutants --in-diff <the package diff>` and fails (exit 16) when the share of caught mutants is below `rust.mutants.min_kill_ratio`.

#### Rule for locked tests in Rust

Put locked acceptance tests in **`tests/`** (integration tests) and list them in `locked_tests:`. Do **not** lock tests that live in a `#[cfg(test)] mod tests` inside `src/`: that file mixes code the agent owns with the tests, so the agent must be allowed to edit it, and the lock cannot work. Unit tests in `src/` are fine as the agent's own extra tests — they are still checked by the anti-cheat rules above.

### 3. The harness is tested

- `tests/` — a pytest suite for the pipeline and the gate (config, globs, seal and CRLF stability, every gate exit code, Python and Rust anti-cheat rules, syn ↔ builtin parity, process-tree timeouts, locks, setup/update, launcher decisions, cargo-mutants threshold).
- `examples/rust-demo/` — a small Rust crate with one work package; `tests/test_e2e_rust.py` installs the harness into a copy and runs the full cycle with a scripted fake agent: gate OK → merge → `drive` → FINAL gate (fmt + clippy + tests), and rejection of: a file outside `owns`, a modified or deleted locked test, `#[ignore]` / empty / assertion-less / `todo!()` / cfg-disabled tests, a crate-level `allow`, a protected file, `Cargo.toml`, a spec changed after sealing, and a branch changed after verification. On macOS and Linux it also checks that the OS sandbox blocks a write outside the worktree.
- `.github/workflows/harness-tests.yml` — CI on `ubuntu-latest`, `windows-latest`, `macos-latest` (+ Python 3.9 on Linux), and a job that runs the real `cargo-mutants`.

Run it locally: `python -m pip install pytest && python -m pytest -q`.

### Gate exit codes

`0` pass · `2` spec not found · `3` uncommitted changes · `4` no commits / bad base · `5` protected or guarded file · `6` outside `owns` · `7` report missing · `8` test tampering · `9` a Verify/`done_when`/project command failed · `14` sealed input changed (pipeline) · `15` config error · `16` cargo-mutants below threshold.

### Known limits

- Windows isolation is weaker (see above).
- `.claude/settings.json` (installed into projects) keeps the upstream `bypassPermissions` mode with `Bash(*)`; its deny list is easy to get around. Harden it for your project.
- Static anti-cheat checks cannot prove a test is meaningful (`assert!(true)` passes them). Use locked acceptance tests and, for Rust, the optional cargo-mutants check.
- The Rust checks look at `#[test]`-style functions; tests generated by macros (e.g. `proptest! { ... }`) are not analysed one by one.
