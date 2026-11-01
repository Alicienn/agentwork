# Design Exploration

Use alongside [big-work](big-work.md), [plan-then-execute](plan-then-execute.md),
or [parallel-agents](parallel-agents.md) when intent leaves meaningful semantic
branches, or when the human requests design exploration. Read the
[operating contract](_contract.md). Small, already specified edits need no tree.

## Record and Authority

Create `.ai/plans/<task>-design.md` on demand with sections for axioms, tree,
node records, assumption audit, questions, and autonomy contract. Keep rationale
out of the tree. Store review findings in the task plan or existing worker
reports; link durable cross-task decisions from `.ai/decisions.md`.

Human intent and explicit confirmation authorize semantics. Agent deductions
remain distinguishable from that intent even after confirmation. Record the
source statement or approval, not merely "approved". Existing accepted behavior
is a preservation constraint; observed implementation alone does not establish
that it is accepted.

| Type | Meaning |
| --- | --- |
| AXIOM | Explicit human intent, requirement, invariant, constraint, or non-goal. |
| INFERENCE | Conclusion derived from cited axioms or fixed choices; not a new axiom. |
| CHOICE | Two or more materially different alternatives; omit trivial engineering forks. |
| ASSUMPTION | Agent-added interpretation of incomplete information; expose origin and impact. |
| QUESTION | Uncertainty that cannot be resolved from accepted information. |

| State | ASCII token | Implementation permission |
| --- | --- | --- |
| FIXED | `[F]` | Human-confirmed; preserve semantics and escalate contradictions. |
| PROVISIONAL | `[~]` | Exploration hypothesis; does not authorize new observable behavior. |
| OPEN | `[?]` | Analyze/propose; do not silently implement an alternative. |
| DEFERRED | `[D]` | Explicitly postponed; do not resolve or commit architecture to an answer. |
| REJECTED | `[X]` | Do not reintroduce unless explicitly reopened and subsequently authorized. |
| FREE | `[*]` | Human-delegated engineering choice within stated constraints; prefer simplicity and reversibility. |

Type and state are independent. An accepted assumption retains its origin/type;
confirmation changes its state. A reopened axiom is no longer operative authority
for affected work. Agents must not grant themselves FREE or FIXED status.
Provisional engineering alternatives under a FREE node may be selected within its
bounds: authority comes from that delegation, not from the alternative's provisional
state. Record the selection without claiming human confirmation.

Each meaningful node has `id`, `type`, `status`, `title`, `origin`, `impact`,
and `parent`. IDs are stable and never reused; `ROOT` is a structural sentinel.
Optional fields: `rationale`, `alternatives`, `affects`, `depends_on`, `constraints`,
`evidence`, `decision`, `notes`. Record cross-branch dependencies with `depends_on`.
Use Markdown fields or a table; no schema tooling is required. For example:

```text
id: C12
type: CHOICE
status: OPEN
title: Identity model
origin: A3; exploration pass 1
impact: HIGH
parent: ROOT
alternatives: C12.A, C12.B
```

Impact: LOW is local, reversible, and does not materially change observable
semantics. MEDIUM affects multiple components or meaningful implementation cost
within accepted semantics. HIGH affects user-visible behavior, public APIs, data
models, architecture, security, persistence, synchronization, compatibility,
deployment, or failure behavior. When uncertain, expose the uncertainty.

Render a compact ASCII tree with IDs, tokens, type, impact, and short title.
Alternatives are nodes with their own records; records are authoritative and the
tree must agree with them. See the [fictional example](design-example.md).

## Explore and Steer

0. Extract explicit axioms, non-goals, and constraints with provenance. Do not
   strengthen vague intent into a requirement.
1. Derive provisional hypotheses, surface materially different alternatives,
   identify dependencies, and estimate impact. Optimize for important forks per
   line of prose; stop expanding low-value detail.
2. After every pass, render the tree and audit new assumptions by impact. List
   potential hidden assumptions too, especially failure behavior, identity,
   persistence, defaults, dependencies, and compatibility. Separate deductions
   from human statements. Batch unresolved questions with options and consequences.
3. Let the human steer using these conceptual operations (no CLI required):

| Operation | Effect |
| --- | --- |
| `explore <node>` | Generate alternatives in that subtree; preserve existing fixed constraints. Request reopening if changing them is necessary. |
| `fix <node-or-branch>` | Record explicit acceptance and selected alternatives. A parent alone does not fix descendants. For a branch, enumerate accepted IDs; do not approve mutually exclusive alternatives together. |
| `reject <node-or-branch>` | Record rejection and its explicit scope; descendants cannot authorize work through a rejected ancestor. |
| `defer <node-or-branch>` | Postpone the specified scope; block implementation that would resolve it. |
| `reopen <node>` | Remove affected authorization for reconsideration; retain prior decision history. |
| `free <node>` | Record explicit engineering delegation, bounds, and constraints; never overrides fixed semantics. |
| `show tree` | Render current compact navigation tree. |
| `show unresolved` | List OPEN, PROVISIONAL, and DEFERRED nodes with blockers and dependencies. |
| `show assumptions` | List assumptions with origin, impact, status, including confirmed ones. |
| `show high-impact` | List all HIGH nodes with state and outstanding decisions. |

Operations reflect human instructions or already explicit authorization, not an
agent's unilateral acceptance of its proposals. If branch scope or selection is
ambiguous, propose the affected IDs before changing authority. Unselected choices
are not automatically rejected unless the human's decision excludes them.

## Re-Explore a Subtree

"Re-explore C12" reopens C12 and the affected descendants, not the entire design.
Set C12 to OPEN and preserve ancestors and unrelated siblings. Identify invalidated
descendants and
mark their authorization withdrawn (OPEN), retaining history; mark replaced
alternatives REJECTED with a replacement reference and the steering instruction.
Generate new alternatives only inside the subtree. Do not reuse old IDs.

Report any contradiction outside the subtree as `UPSTREAM CONFLICT` with the
affected IDs. Do not rewrite those nodes. Flag external dependents as blocked in
the questions/contract section without changing their design; independent work
can continue. Update the accepted contract before resuming affected implementation.

## Freeze and Implement

4. Freeze only an implementable scope at the existing plan approval gate. Record:
   accepted document revision or commit; human approval evidence; FIXED nodes;
   FREE bounds; preserved existing behavior; excluded OPEN/PROVISIONAL/DEFERRED
   branches; REJECTED prohibitions; dependencies; verification and baseline.
   Approval of an explicit contract need not be requested twice. OPEN or DEFERRED
   nodes may remain if the selected work does not require resolving them.
5. Implement within that contract, following [_contract.md](_contract.md).
   Every new externally observable behavior and test expectation must identify an
   authorizing accepted node. FREE engineering choices implement accepted semantics;
   they do not license new product behavior. Record non-obvious choices and low-impact
   assumptions in the task record; preserve existing behavior where applicable.
   Deferred placeholders/interfaces require explicit permission and cannot choose
   a deferred answer indirectly. If the accepted revision changes, recheck affected
   packages before continuing; a stale contract is not authority.

## Independent Validation and Repair

6. Assign roles explicitly. Roles may rotate across Antigravity (Agy), Claude,
   Codex, or other agents: specification/exploration; independent acceptance-test
   derivation; implementation; adversarial semantic review; code validation/repair.
   One possible assignment is Claude or Codex for exploration, a different agent
   for tests, Agy for implementation, Claude for semantic review, and Codex for
   repair. These are roles, not rankings or a claim of launcher support.
   Use the parallel harness only when requested/authorized; a sequential handoff
   is sufficient. The same agent must not be sole author and sole validator of its
   assumptions. If no independent agent is available, report that gap and obtain
   human validation; self-review remains useful but is not independent validation.
7. Treat implementation as potentially wrong. Compare accepted design and prior
   accepted behavior against tests and the actual baseline diff. Ask: "Which
   design node authorizes this behavior?" Tests are evidence, not automatically
   authoritative specification; challenge unsupported test expectations too.

Record findings with ID, category, severity, file/evidence, affected/absent design
node, and a specific repair or decision request:

| Category | Look for |
| --- | --- |
| BUG | Incorrect implementation of authorized behavior. |
| SPEC-DRIFT | Divergence from accepted semantics. |
| UNAUTHORIZED-BEHAVIOR | New observable behavior with no design authority. |
| HIDDEN-ASSUMPTION | Unrecorded interpretation influencing implementation. |
| OVERDESIGN | Unneeded complexity or speculative flexibility. |
| MISSING-TEST | Missing evidence for accepted behavior. |
| ARCHITECTURAL-COMMITMENT | A costly structural choice outside delegated bounds. |
| QUESTION | Uncertainty requiring clarification, not a presumed defect. |

8. Validate findings against the contract, then assign targeted repair (prefer a
   different agent where practical): "Fix R3 and R5 within accepted design."
   Re-run relevant checks and re-review those changes. A finding needing new
   semantics returns to human steering; a reviewer cannot approve that change
   by calling it a bug. Finish with behavior/node provenance, FREE choices,
   remaining unresolved nodes, validation gaps, and the git checkpoint.

```text
axioms -> exploration -> tree + audit -> human steering
                         ^                    |
                         +-- subtree reopen --+
                                              |
                                      autonomy contract
                                              |
                           independent tests -> implementation
                                              |
                              adversarial review <-> targeted repair
                                              |
                                   final review + checkpoint
```
