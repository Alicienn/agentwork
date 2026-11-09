# agentwork

> Fork of [apelov/agentwork](https://github.com/apelov/agentwork) (MIT, © apelov). Adds **Rust** support and runs on **Windows, Linux and macOS**.

A deterministic harness for running AI coding agents (`claude`, `codex`, `agy`, `claude-local`, `pi-code`, …) on Markdown work packages, each in its own git worktree, behind a mechanical gate.

- **Interactive mode**: read `AGENTS.md`, then `.ai/session.md`.
- **Autonomous mode**: follow `Bootstrap an Autonomous Multi-Agent Repository.md`; the build runs with `pipeline.py drive`.
- **Privacy profiles**: `openrouter-first`, `sensitive`, `balanced`, `local-only`.

## Quickstart

Needs Python 3.9+ and git (plus `cargo`, `rustfmt`, `clippy` for Rust).

```bash
./setup.sh /path/to/project --profile balanced      # Windows: setup.cmd C:\path\to\project --profile balanced
./agentwork/scripts/driver.sh --build-all           # Windows: agentwork\scripts\driver.cmd --build-all
```

Every launcher calls `python agentwork/pipeline.py <command>` (`setup`, `gate`, `launch`, `split`, `driver`, `drive`, `seal`, …).

## What the fork changes

**Cross-platform.** The bash scripts are now Python (`agentwork/awlib/`, no dependencies), with thin `.sh` and `.cmd` launchers. Timeouts kill the whole process tree on Windows too, and seals are stable across CRLF/LF checkouts.

**Isolation.** `isolation_mode: auto` uses Seatbelt (macOS) or bubblewrap (Linux).

> ⚠️ **Windows has no OS sandbox.** Agents run in `worktree-only` mode, with your user rights; a warning is shown at every launch. Only the worktree and the gate protect the project. Use WSL2 or a VM for untrusted agents. `isolation_mode: worktree-seatbelt` refuses to run without a sandbox.

**Rust.** Set `languages:` in `project.yaml` (`auto` detects `Cargo.toml` / `pyproject.toml`). Python support is kept.
- Default commands: `cargo test` (or `cargo nextest`), `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`.
- The gate parses changed `.rs` files with `syn` (with a Python fallback) and rejects `#[ignore]`, tests that are empty or have no assertion, `todo!()` in tests, tests disabled by `#[cfg(any())]`, removed tests, and new `#![allow(...)]`.
- `Cargo.toml`, `build.rs`, and clippy/rustfmt/cargo config may change only if listed literally in `owns:`.
- `pipeline.py context` extracts Rust signatures.
- Optional `cargo-mutants` check with a minimum kill ratio (`rust.mutants`, off by default).

**Locked tests in Rust** go in `tests/` (integration tests), not in a `#[cfg(test)] mod tests` inside `src/`, which mixes owned code and tests.

## Tests

```bash
python -m pip install pytest && python -m pytest -q
```

The suite covers the pipeline, every gate rule, and an end-to-end cycle on `examples/rust-demo`. CI runs it on Ubuntu, Windows and macOS.

## Known limits

- Weaker isolation on Windows (see above).
- `.claude/settings.json` keeps `bypassPermissions` with `Bash(*)`: harden it for your project.
- Static checks cannot prove a test is meaningful; use locked tests and `cargo-mutants`.
