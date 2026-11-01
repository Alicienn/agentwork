---
id: WP01
name: <short-slug>
kind: implementation
tier: code
rung: 1
depends: []
inputs:
  - docs/design/DESIGN.md
outputs:
  - src/<path>
  - tests/test_<name>.py
owns:
  - src/<path>
  - tests/test_<name>.py
locked_tests: []
done_when:
  - <test command, e.g. pytest -q>
review_required: false
---
# WP<NN> — <short imperative title>

Tier: code
Verify: <one shell command run by the harness after the agent exits, e.g. pytest -q>

<One line: the outcome this package delivers.>

> Copy this file to `WP<NN>-<slug>.md` and fill in the YAML frontmatter and sections below.
> To run packages safely, make concurrent packages own a **disjoint set of files** (`owns:`).
>
> **Tier** (`plan` | `code` | `mini-code`) determines the brief (`CONTEXT.md` vs `CONTEXT-lite.md`)
> and default capability rung. For `mini-code` or Test-First packages, place pre-seeded stubs or
> locked acceptance tests in `agentwork/packages/<this-package-name>.files/` mirroring repo paths,
> and list locked test paths under `locked_tests:`.

## Why

<The problem or need this addresses, in 2–4 sentences.>

## Deliverables

1. <concrete, checkable change>.
2. <concrete, checkable change>.

## Owns (touch only these)

- `src/<path>` — <what changes here>.
- `tests/test_<name>.py` — <unit/acceptance tests>.

Do **not** modify files outside `owns:`.

## Design authority (optional — include when using design-exploration.md)

- Accepted intent/design: <path, revision, node IDs, and approval evidence>.
- FIXED: <relevant semantic constraints and accepted prior behavior>.
- FREE: <delegated engineering choices and their bounds, or none>.
- Excluded/prohibited: <OPEN, PROVISIONAL, DEFERRED, REJECTED nodes>.

## Constraints / invariants

- Honor the hard invariants in `CONTEXT.md` and `agentwork/state/PROJECT.md`.
- Commit locally on your worktree branch; **never push**.
- Write `agentwork/reports/wp<nn>.md` summarizing changes, verification output, and any non-obvious choices.
