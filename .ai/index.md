# AI Project Index

`.ai/session.md` and `.ai/commands.md` are always loaded by default (see `AGENTS.md`).

Read these on demand:

- `.ai/decisions.md` — read when the user references a prior decision; write when you make a project-level choice worth preserving.
- `.ai/git.md` — read before committing; review on substantial work.
- `.ai/workflows/` — read when selecting or executing a workflow; see `AGENTS.md` for the selection rubric. `_contract.md` is shared by `plan-then-execute` and `big-work`; `parallel-agents.md` delegates to several coding agents via `agentwork/`.
- `agentwork/` — the parallel-agents harness: `CONTEXT.md` (shared brief), `packages/WP*.md` (work-package specs), `scripts/launch.sh` / `launch.cmd` (runner; logic in `pipeline.py launch`), and `reports/` (per-worker results). `agentwork/logs/` is gitignored. See `agentwork/README.md`.
- `.ai/plans/` — durable plan artifacts produced during `big-work` or `plan-then-execute`.
- `.ai/workflows/design-exploration.md` — read for meaningful design uncertainty,
  tree operations, autonomy contracts, and adversarial review. Task design state
  lives in `.ai/plans/<task>-design.md`, created only when needed.
- `.ai/workflows/design-example.md` — optional fictional worked example.
- `.ai/drafts/` — drafts in progress (IETF drafts, documents, long-form research).
- `.ai/notes/` — archived session notes; rotate `session.md` content here when it grows long.
- `.ai/scratch/` — ephemeral local notes; ignored by git.
