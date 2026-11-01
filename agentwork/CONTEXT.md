# <PROJECT> — Shared Agent Brief

You are a coding agent working on **<PROJECT>**, <one-line description>. This
brief is prepended to every work-package prompt. Read it, then do the work
package that follows.

> Fill in the `<...>` placeholders once per project. The **Workspace
> discipline** section at the bottom is fixed — keep it.

## What this project is (and its north star)

<Two or three sentences: what the project is, and the single most important
value or outcome to protect. State priorities in order so an agent can resolve
trade-offs the same way you would.>

1. **<Priority 1>** — <what it means in practice>.
2. **<Priority 2>** — <…>.
3. **<Priority 3>** — <…>.

## Layout

<Where things live and what each part is for — the map an agent needs to find
its way. List the packages/dirs/modules a WP is likely to touch.>

- `<path/>` — <what it is>.
- `<path/>` — <what it is>.

## How to run + verify

```sh
<setup command>            # e.g. uv venv .venv && uv pip install -e . pytest
<test command>             # the check that must stay green
<run command>              # how to run it locally, if applicable
```

**Sandbox note:** if you cannot bind ports, reach a network service, or use a
container, do what you can (unit tests, syntax checks, static analysis) and say
so in your report; the reviewer runs the live checks the sandbox can't.

## Hard invariants

<The rules that must never be violated, regardless of the WP. Keep this short
and absolute.>

- <invariant>.
- <invariant>.
- The project's checks stay green; run every check your change could affect.
- Follow the package's accepted design revision, constraints, and FREE bounds;
  trace new behavior/tests to its authority. Unspecified behavior is not permission
  to invent. Report blocking unresolved semantics; see
  `.ai/workflows/design-exploration.md` for design and review rules.

## Workspace discipline (fixed — do not change)

You are on a dedicated git branch/worktree. Commit locally as you finish
coherent units (`feat(...)`, `fix(...)`). **Never push.** Touch only the paths
your package declares. Finish by writing `agentwork/reports/<wp-id>.md`: what
you changed, how you verified it (paste test / check output), and anything the
reviewer must confirm live.
