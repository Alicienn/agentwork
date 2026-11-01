# Big Work Workflow

Use this mode for substantial writing, IETF drafts, planning, research, or mixed tasks. For substantial coding tasks, use `plan-then-execute.md` instead.

Read `_contract.md` before starting. It defines what is allowed, what requires asking, and when to interrupt.

## 1. Clarify Intent

Identify:

- Goal
- Non-goals
- Constraints
- Acceptance criteria
- Audience or users
- Risks and unknowns

Ask important questions upfront instead of interrupting later.

For meaningful semantic branches, use [design-exploration.md](design-exploration.md)
to extract axioms, explore alternatives, audit assumptions, and obtain human
steering. Keep these in the task's design record; link it from the plan.

## 2. Plan

Produce a concise plan in `.ai/plans/` (one file per task):

- Approach
- Files likely to change
- Verification method
- Stop conditions
- Expected git checkpoint

Wait for user approval before substantial execution.

When a design record exists, include its accepted revision, FIXED/FREE scope,
excluded unresolved branches, and baseline in that approval. Existing explicit
approval satisfies this gate; do not request it again for the same scope.

## 3. Execute

Work autonomously per `_contract.md`. Keep `.ai/session.md` current during long runs so progress survives a context reset. Place in-flight drafts under `.ai/drafts/` and exploratory notes under `.ai/scratch/`.

## 4. Verify

Use proof appropriate to the task:

- Code: tests, lint, typecheck, build, app smoke test.
- IETF drafts: build output, references, terminology, section consistency, normative language, idnits when available.
- Writing: structure, clarity, consistency, citations, final diff.
- Research or planning: sources, assumptions, alternatives, decision record.

If a check cannot be run, state why.

Use the design reference's adversarial review categories and independent validation
when design exploration applies. Check behavior and test provenance against the
accepted contract; repair validated finding IDs and recheck affected work.

## 5. Finish

Before stopping:

- Update `.ai/session.md`.
- Review git status and diff.
- Create a local git checkpoint for substantial work using `.ai/git.md`.
- Push only if the user requested or approved it.
- Summarize what changed, what was verified, and any remaining risks.
