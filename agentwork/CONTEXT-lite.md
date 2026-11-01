# <PROJECT> — Shared Agent Brief (lite)

You are a coding agent working on **<PROJECT>**, <one-line description>. Do the
work package that follows. Everything you need is in this brief and that package.

> Prepended to `Tier: mini-code` packages instead of `CONTEXT.md`. Fill in the
> `<...>` placeholders once per project. The **Workspace discipline** section at
> the bottom is fixed — keep it.
>
> **Why a second brief:** a small model has a limited context window and finite
> attention. The full `CONTEXT.md` carries history, rationale and hard-won
> platform gotchas that help a large model make good judgement calls — and that
> crowd out the task for a small one. Keep this file to what is needed to write
> correct code for a *closed* task. Rule of thumb: **one screen, no history, no
> rationale, no war stories.** If a detail only matters for a decision a
> small-tier package should not be making, it belongs in `CONTEXT.md`, not here.

## The vocabulary

<The handful of types / names / units an agent must use to fit in. Point at the
one or two files that define them. Nothing else.>

- `<path>` — <the shared types or vocabulary; read this first>.

## Where your work goes

<Only the paths a small package plausibly touches. Not a full project map.>

- `<path/>` — <what lives here>.

## Rules

- Touch **only** the files your package lists under "Owns".
- Do not change shared types, public interfaces, or dependency manifests. If you
  think one is wrong, report it and pause affected work unless the package gives
  an authorized fallback. Do not invent substitute semantics.
- Follow the package's design contract; unresolved choices are not yours to settle.
  Trace observable changes/tests to its authority and record non-obvious FREE choices.
- <one or two absolute, project-specific rules — style, determinism, no network,
  whatever would silently break things if ignored>.

## How to verify

```sh
<the exact check commands, e.g. test runner, linter>
```

Both must pass before you are done. If a check cannot run in this environment,
say so plainly in your report rather than skipping it silently.

## Workspace discipline (fixed — do not change)

You are on a dedicated git branch/worktree. Commit locally as you finish
coherent units (`feat(...)`, `fix(...)`). **Never push.** Touch only the paths
your package declares. Finish by writing `agentwork/reports/<wp-id>.md`: what
you changed, how you verified it (paste test / check output), and anything the
reviewer must confirm live.
