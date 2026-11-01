# Plan Then Execute Workflow

Use this mode for substantial coding tasks where the user wants careful upfront planning and autonomous implementation. For substantial non-code or mixed work, use `big-work.md`.

Read `_contract.md` before starting. It defines what is allowed, what requires asking, and when to interrupt.

## Phase 1: Intent and Scope

Clarify:

- Goal
- Non-goals
- Acceptance criteria
- Relevant files or modules
- Test strategy
- Risks and unknowns

Prefer asking important questions upfront instead of interrupting later.

For meaningful design branches, follow [design-exploration.md](design-exploration.md):
extract axioms, explore alternatives, audit assumptions, and let the human steer.

## Phase 2: Plan

Produce a concise plan in `.ai/plans/` (one file per task):

- Current understanding
- Proposed implementation
- Files likely to change
- Tests to add or update
- Verification commands
- Known risks
- Stop conditions

Wait for user approval before implementation.

If using a design tree, include its accepted revision, FIXED/FREE scope, excluded
unresolved branches, dependencies, and baseline in the plan approval. Do not ask
again for scope already explicitly approved.

## Phase 3: Tests (recommended, not required)

When practical:

- Add or update tests before implementation.
- Prefer characterization tests when modifying existing behavior.
- Confirm existing tests pass when feasible.
- Trace new observable expectations to accepted intent/design nodes. Tests are
  evidence, not automatic specification. With design exploration, assign test
  derivation independently of the specification author where available and record
  any validation gap as described in the design reference.

Skip test-first when prototyping or exploring. If you skip, state why and define another verification method.

## Phase 4: Autonomous Execution

Implement per `_contract.md` without routine interruptions. Keep `.ai/session.md` current during long runs so progress survives a context reset.

## Phase 5: Verification and Handoff

Before finishing:

- Apply the design reference's adversarial review when using a design contract;
  obtain independent validation and repair named, validated findings within scope.
- Run relevant tests and checks, or explain why not.
- Summarize changed files.
- Summarize behavior implemented.
- Record remaining risks.
- Update `.ai/session.md`.
- For substantial work, create a git checkpoint using `.ai/git.md`.
