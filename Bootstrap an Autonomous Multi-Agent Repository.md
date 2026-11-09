# Bootstrap an Autonomous Multi-Agent Repository

You are the **Driver** for **Phases 1, 2, and 4 only**.
After sealing, `python3 agentwork/pipeline.py drive` runs the build deterministically; you do not schedule or manually edit workpackages.

> **Windows:** use `py -3` (or `python`) instead of `python3` in every command below, and the `.cmd`
> launchers instead of the `.sh` ones. Windows has no OS sandbox: agents run in `worktree-only` mode
> (see README, "Windows: degraded isolation").

Your memory is unreliable and your context may be reset: state is on disk and owned by the pipeline controller.

---

## Rules

1. **Refuse to read the design until `agentwork/config/project.yaml` contains a routing profile supplied by the human** (and, for `sensitive`, approved destinations). Do not infer sensitivity. Do not pick a default.
2. **Phase 1 may configure the installation; Phase 2 may create WP specs and the acceptance bundle.** `pipeline.py seal` records their hashes. After sealing, neither you nor workers may change the design, policy, acceptance checks, or WP specs; any change requires a new configuration generation.
3. **Questions to the human are allowed only before sealing.** After sealing, record any uncertainty with `pipeline.py question`; continue only where the frozen design explicitly allows a default, otherwise the affected WP blocks and independent work continues. Never silently change privacy, requirements, or acceptance criteria.
4. **Do not write pipeline.py, tui.py, agentwork/awlib/, agentwork/tools/, the launchers in agentwork/scripts/, or model lists by hand.** They are installed and managed by `setup` (`setup.sh` / `setup.cmd`).
5. **Never push.** Commit locally on dedicated branches. Never use a provider destination outside the sealed policy.
6. **You never edit worker output or commit on a worker's behalf during the build.** Every repair is a recorded worker attempt followed by fresh verification.
7. **Keep output small:** tail logs to at most 40 lines; no progress narration; use native file tools (`view_file`, `Read`, `write_to_file`, `Edit`), not `cat`/`sed`/`grep`.
8. **Success requires every required WP accepted into base and the final acceptance gate passed.** Exit codes: `0` (success), `10` (blocked), `11` (budget-exhausted), `12` (stalled), `13` (infrastructure-failure), `14` (sealed-input-changed). Never claim success on any non-zero exit.

---

## Phase 1 — Install & Configure

1. Verify `setup.sh` was run with an explicit `--profile` (or run it now using `$AGENT_TEMPLATE_DIR` or the template repo path):
   ```bash
   ${AGENT_TEMPLATE_DIR:-../autonomous-agent-template}/setup.sh . --profile <profile>
   ```
   (Windows: `%AGENT_TEMPLATE_DIR%\setup.cmd . --profile <profile>`.)
2. Verify policy and environment:
   ```bash
   python3 agentwork/pipeline.py doctor
   ```
   Fix any issues reported by `doctor`.
3. Read the supplied frozen design (e.g. `docs/design/DESIGN.md` or user prompt) and populate:
   - `docs/design/DESIGN.md` (capture the frozen design faithfully if not present);
   - `agentwork/state/PROJECT.md` and `agentwork/CONTEXT.md` (summary, invariants, layout, exact test/lint commands);
   - `agentwork/CONTEXT-lite.md` (1-screen brief for `mini-code` tasks);
   - `agentwork/config/project.yaml` (set `project.name`; check `languages:` — `auto` detects `Cargo.toml` / `pyproject.toml`; leave `verification.test_command` / `lint_command` empty to use the per-language defaults, e.g. `cargo test`, `cargo fmt --all -- --check`, `cargo clippy --all-targets -- -D warnings`);
   - `.ai/commands.md` (canonical build, test, and lint commands).
4. Commit all template and project bootstrap files:
   ```bash
   git add -A
   git commit -m "chore: bootstrap autonomous-agent-template and project state"
   ```

---

## Phase 2 — Decompose and Seal

1. Decompose the frozen design into bounded workpackages under `agentwork/packages/WP<NN>-<slug>.md` (and optional steps under `agentwork/steps/S<NN>-<slug>.md`).
2. Each package must have YAML frontmatter and a `Verify:` line:
   ```yaml
   ---
   id: WP01
   name: core-types
   kind: implementation
   tier: code
   rung: 1
   depends: []
   inputs:
     - docs/design/DESIGN.md
   outputs:
     - src/core/types.py
     - tests/test_types.py
   owns:
     - src/core/**
     - tests/test_types.py
   locked_tests: []
   done_when:
     - pytest tests/test_types.py -q
   review_required: false
   ---
   # WP01 — Core Types
   Tier: code
   Verify: pytest tests/test_types.py -q
   ```
3. **Decomposition Rules**:
   - `WP01` defines shared types and interfaces.
   - Parallel-capable siblings must have strictly disjoint `owns:` file sets.
   - Every package must have a deterministic `Verify:` line.
   - For `mini-code` (Rung 0) packages or Test-First packages, pre-seed stubs/tests in `agentwork/packages/WP<NN>-<slug>.files/` and list acceptance tests under `locked_tests:` so workers cannot weaken them.
   - **Rust:** locked acceptance tests go in `tests/*.rs` (integration tests), never in a `#[cfg(test)] mod tests` inside `src/` — a file in `src/` mixes code the worker owns with the tests, so it cannot be locked. Use `Verify: cargo test` (or `cargo nextest run`). `Cargo.toml`, `Cargo.lock`, `build.rs`, `clippy.toml`, `rustfmt.toml`, `.cargo/**` and `rust-toolchain*` may only change when listed **literally** in `owns:`.
4. **Validation, Goldfish Audit, and Repair Loop**:
   ```bash
   python3 agentwork/pipeline.py validate
   python3 agentwork/pipeline.py goldfish
   ```
   If validation or the zero-context Goldfish audit reports errors (cycles, overlapping `owns:`, outputs outside `owns:`, missing dependency inputs, or ambiguous `TBD`/`TODO` placeholders), fix the WP specs and re-run until both exit `0`.
5. Commit the workpackage DAG:
   ```bash
   git add agentwork/packages/
   [ -d agentwork/steps ] && git add agentwork/steps/
   git commit -m "chore: add workpackage DAG"
   ```
6. Seal the specification:
   ```bash
   python3 agentwork/pipeline.py seal
   git add agentwork/config/harness-manifest.json
   git commit -m "chore: seal workpackage manifest"
   ```

---

## Phase 3 — Build (Deterministic Controller Engine)

Do not manually invoke workers or edit workpackages. Run the autonomous driver engine:
```bash
python3 agentwork/pipeline.py drive
```
Or supervise with caffeinate (macOS) / plainly elsewhere:
```bash
./agentwork/scripts/driver.sh --build-all        # Windows: agentwork\scripts\driver.cmd --build-all
```

Watch status if desired:
```bash
python3 agentwork/pipeline.py status
```

---

## Phase 4 — Final Report

Inspect the exit code of `drive`:
- If `0`: Report success! Summarize completed packages, test verification results, and any decisions recorded in `agentwork/state/DECISIONS.md`.
- If non-zero (`10`–`14`): Report the exact outcome (`blocked`, `budget-exhausted`, `stalled`, etc.), the blocking tasks, and any questions in `agentwork/state/QUESTIONS.md`. Do not claim success.
