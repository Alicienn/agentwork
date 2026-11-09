# Agent Operating Contract (AGENTS.md)

This repository supports both **Interactive / Exploration Mode** (default when collaborating with a human) and **Autonomous Multi-Agent Pipeline Mode** (when invoked as a Driver or Worker on a bounded workpackage).

---

## Mode Selection

1. **Autonomous Worker Mode**: If you are invoked on a specific workpackage (`WP<NN>`), follow **Worker Rules** below and stop immediately after completing that package.
2. **Autonomous Driver Mode**: If you are instructed to bootstrap or drive an autonomous build (or follow `Bootstrap an Autonomous Multi-Agent Repository.md`), follow **Driver Rules** below.
3. **Interactive / Exploration Mode (Default)**: For interactive sessions, design exploration, research, IETF drafts, or general edits, follow **Interactive & Design Exploration Rules** below.

---

## Interactive & Design Exploration Rules

- **Session & Commands**: Read `.ai/session.md` before starting (if it is the template stub, treat the user's prompt as the starting point). Consult `.ai/commands.md` before running build/test/lint commands. See `.ai/index.md` for on-demand files.
- **Small Edits by Default**: Preserve user changes, follow existing project patterns, and make small, focused edits. Update `.ai/session.md` at the end of a meaningful work unit or before a context reset.
- **Substantial Work Workflows**: Switch into a workflow in `.ai/workflows/` when work spans multiple files or sessions:
  - `.ai/workflows/plan-then-execute.md` — substantial coding tasks with upfront planning.
  - `.ai/workflows/big-work.md` — substantial writing, IETF drafts, research, or mixed tasks.
  - `.ai/workflows/parallel-agents.md` — decompose work into file-disjoint packages in isolated git worktrees.
- **Epistemic Steering & Design Exploration**: Unspecified behavior is not permission to invent. When intent leaves meaningful design branches, use `.ai/workflows/design-exploration.md` to track `AXIOM`, `CHOICE`, `ASSUMPTION`, and `QUESTION` nodes (`FIXED [F]`, `FREE [*]`, `OPEN [?]`, `DEFERRED [D]`, `REJECTED [X]`) and let the human steer before freezing `docs/design/DESIGN.md`.

---

## Worker Rules (Bounded Workpackage Execution)

AI sessions in Worker Mode are disposable compute; the repository and pipeline controller are the source of truth.

1. **Read Assigned Task First**: Read your workpackage specification (`agentwork/packages/WP<NN>-*.md`) and `agentwork/state/PROJECT.md` before making any edits.
2. **Strict Ownership Boundaries**: Touch **only** the files listed under `Owns` (or `owns:`) in your package spec, plus your report `agentwork/reports/<wp-id>.md`. Modifying files outside your ownership will cause the mechanical verification gate to reject the entire package.
3. **Protected Files**: You are strictly forbidden from modifying `docs/design/DESIGN.md`, `agentwork/config/**`, `agentwork/state/DECISIONS.md`, `.claude/**`, `.codex/**`, `.agents/**`, `agentwork/awlib/**`, `agentwork/tools/**`, `agentwork/scripts/**`, `.github/**`, `.gitattributes`, and any test runner configurations (`pytest.ini`, `conftest.py` at root, `pyproject.toml` tool sections).
4. **Never Weaken Tests**: Never delete existing test assertions, add `@pytest.mark.skip`, or write empty test bodies (`def test_foo(): pass`). The verification gate inspects test ASTs and rejects stubbed tests. Removing or renaming an existing test is rejected too.
   - **Rust**: never add `#[ignore]` (or `#[cfg_attr(..., ignore)]`), never write a `#[test]` with an empty body or without an assertion (`assert!`, `assert_eq!`, ... or `#[should_panic]`), never leave `todo!()` / `unimplemented!()` in test code, never disable tests with an always-false `#[cfg(...)]` (`any()`, `not(test)`, ...), and never add crate-level `#![allow(...)]` or blanket `#[allow(warnings)]` / `#[allow(clippy::all)]`. Locked acceptance tests live in `tests/*.rs`. Do not touch `Cargo.toml`, `Cargo.lock`, `build.rs`, `clippy.toml`, `rustfmt.toml`, `.cargo/**` or `rust-toolchain*` unless your package lists that exact path in `owns:`. The gate parses Rust with `syn` and rejects all of these.
5. **Commit Locally**: When your task is complete and passing all checks, commit your changes locally on your dedicated worktree branch with a descriptive message (`feat(...)`, `fix(...)`).
6. **Never Push**: Never push to remote git repositories. Integration and promotion to `main` are handled exclusively by the controller.
7. **Write Durable Report**: Write `agentwork/reports/<wp-id>.md` detailing what was changed, test verification output, known limitations, and any non-obvious engineering choices within delegated bounds.
8. **Stop After Assigned Task**: Do your assigned job and stop.

---

## Driver Rules (Phases 1, 2, and 4)

1. **Phase 1 (Configure)**: Ensure `setup.sh` was run with the required `--profile`, capture the frozen design in `docs/design/DESIGN.md`, and configure `PROJECT.md` and `project.yaml`.
2. **Phase 2 (Decompose & Seal)**: Write workpackage specs, validate via `python3 agentwork/pipeline.py validate`, fix any validation errors, and seal via `python3 agentwork/pipeline.py seal`.
3. **Phase 3 (Build)**: Run `python3 agentwork/pipeline.py drive` (or `./agentwork/scripts/driver.sh --build-all`). Do not manually schedule or edit workpackages during Phase 3.
4. **Phase 4 (Report)**: Inspect the terminal outcome of `drive` and report results to the human.
