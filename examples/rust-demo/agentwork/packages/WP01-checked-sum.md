---
id: WP01
name: checked-sum
kind: implementation
tier: code
rung: 1
depends: []
inputs:
  - docs/design/DESIGN.md
  - src/lib.rs
outputs:
  - src/lib.rs
owns:
  - src/lib.rs
  - tests/extra.rs
locked_tests:
  - tests/acceptance.rs
done_when:
  - cargo test
review_required: false
---
# WP01 — implement checked_sum

Tier: code
Verify: cargo test

Implement `checked_sum` in `src/lib.rs` so the locked acceptance tests in `tests/acceptance.rs` pass.

## Owns (touch only these)

- `src/lib.rs` — the implementation.
- `tests/extra.rs` — optional extra tests of your own.

Do **not** modify `tests/acceptance.rs` (locked) or any file outside `owns:`.
Write `agentwork/reports/wp01.md` when done.
