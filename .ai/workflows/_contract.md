# Operating Contract

Shared by `big-work.md` and `plan-then-execute.md`. Read this before either workflow.

## Principle

Spend meaningful time upfront clarifying intent. After the user approves the plan, work autonomously until the task is complete. Do not interrupt for routine choices already covered by the approved plan.

**UNSPECIFIED DOES NOT MEAN PERMISSION TO INVENT.** Preserve existing accepted
behavior. New observable behavior needs explicit human intent or accepted design
authority; implementation and passing tests cannot supply that authority.
When meaningful design branches exist, use [design-exploration.md](design-exploration.md)
for node states, traceability, subtree steering, and independent review.

## Autonomy Within the Contract

Decide local, reversible, low-impact implementation details autonomously when
compatible with fixed constraints and delegated bounds. Record non-obvious choices
and assumptions. Do not stop for trivial choices or treat unspecified details as
FREE delegation. A smallest reversible interpretation is acceptable only when it
does not materially introduce observable behavior or resolve an OPEN, PROVISIONAL,
or DEFERRED semantic question.

Escalate contradictions with fixed decisions, material user-visible ambiguity,
public API/data-model changes, new dependencies/services, security semantics,
expensive-to-reverse choices, or blocking unresolved design outside existing
authorization. Pause affected work while continuing independent authorized work.
Batch non-blocking questions with alternatives and consequences. Suggested design
checkpoints: three HIGH or about ten MEDIUM unresolved branches. A contradiction
or blocked subtree triggers a checkpoint immediately; counts are not permission
to implement unresolved choices.

Prefer modifying existing code, then a local helper, module, abstraction,
framework, and finally dependency, with increasing justification. Do not build
for hypothetical future requirements without accepted design authority. Where
useful, estimate task/diff size in the plan; unexpected growth triggers scope and
architecture reassessment, not an arbitrary universal line limit.

## Ask the user only when

- The requested behavior is ambiguous in a way that changes product or document semantics.
- You would need to discard, overwrite, or revert user work.
- You need credentials, external access, or approval for a risky command.
- The approved plan no longer fits what you found.
- A major scope expansion is required.

## Allowed during autonomous execution

- Read relevant files.
- Edit files within approved scope.
- Add tests, drafts, notes, or supporting artifacts.
- Refactor or reorganize within approved scope.
- Run relevant checks; iterate on failures until acceptance criteria are met.
- Create local git checkpoints (see `.ai/git.md`).

## Not allowed without asking

- Destructive git operations (`reset --hard`, `clean`, discarding changes, force-push).
- Discarding user changes.
- Publishing, pushing, or sharing externally unless already approved.
- Adding major dependencies.
- Changing public behavior or scope beyond the approved plan.
