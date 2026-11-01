# Parallel Agents Workflow

Use this mode to **delegate** substantial work to several coding agents at once:
decompose it into Markdown **work-package (WP)** specs, run `claude` / `gemini` /
`codex` headless against them in **isolated git worktrees**, then review and
merge. You (the driving agent) are the **orchestrator**; the launched agents are
**workers**. The harness lives in `agentwork/` (see `agentwork/README.md`).

Read `_contract.md` and `.ai/git.md` before starting. This workflow **plans like
`plan-then-execute`** — get the decomposition approved before spending agents.

## When to use it

- The task decomposes into **file-disjoint** units that are worth running in
  parallel, and the user wants to spend several agents / their credits.
- Prefer `plan-then-execute` (one agent, sequential) when the work is small,
  tightly coupled across files, or not worth the orchestration overhead. Prefer
  `big-work` for writing/research.

## Phase 1: Decompose (get approval)

**Establish who will implement these packages before you write any of them.**
Reuse an explicit choice from the session; otherwise ask.
The answer changes how every spec must be written, and is expensive to discover
afterwards. Offer two options, `pi-code` as the default:

| Answer | Write packages that are… |
|---|---|
| **`pi-code`** (default) | **closed** — interfaces fixed; a stub and the tests shipped in `packages/<name>.files/`; nothing left to design; a `Verify:` line |
| **`hosted`** (claude/codex/gemini) | **bounded engineering freedom** — accepted semantics and constraints stated; implementation choices may be FREE; no stub |

A planner cannot ask this itself — it runs headless. So it is the orchestrator's
job to establish it in this phase. Pass the answer through as
`split.sh --target pi-code|hosted`.

- Sketch the units of work. For each, decide **which files it owns**. Design the
  split so packages own **disjoint** file sets — that is what makes them safe to
  run and merge in parallel. Note any **soft dependencies** and a graceful
  authorized fallback so a package still runs if its dependency hasn't merged yet.
  If a semantic dependency has no accepted fallback, block that package.
- Present the WP breakdown (ids, one-line goals, owned files, dependency order)
  and wait for approval before launching agents.

## Phase 2: Author the specs

- Fill in `agentwork/CONTEXT.md` once for the project (north star, layout, how to
  verify, hard invariants). It is prepended to **every** WP prompt.
- Write one `agentwork/packages/WP<NN>-<slug>.md` per unit from
  `agentwork/packages/WP-template.md`: **Why · Deliverables · Owns (files) ·
  Constraints · Verification · write the report.** Keep each package
  self-contained and its file ownership disjoint from the others.
- Use [design-exploration.md](design-exploration.md) for unresolved semantics.
  Implementation packages carry the accepted revision/IDs, FIXED constraints,
  FREE bounds, prohibited/unresolved scope, and test provenance. Plan-tier workers
  may propose alternatives but cannot accept them on the human's behalf.
- Include the relevant contract excerpt in each package, especially mini-code:
  older projects retain seeded briefs. Do not assume they contain new policy.
  A stale package needs revalidation if its accepted design revision changes.
- Assign independent test/review roles as described in the design reference;
  record missing independent validation instead of treating self-review as enough.

## Phase 3: Launch (parallel)

```sh
agentwork/scripts/launch.sh WP01 codex        &
agentwork/scripts/launch.sh WP02 gemini       &
agentwork/scripts/launch.sh WP03 claude       &
agentwork/scripts/launch.sh WP04 pi-code      &   # local, no API credits
wait
```

- Assign agents however you like; any WP can go to any agent. Spread across
  vendors to parallelize credit use.
- `claude-local` and `pi-code` run local Ollama models and cost **no
  API credits**. Run **at most one** local worker at a time — Ollama serves a
  single model instance, so concurrent local workers queue and each parallel
  slot costs another KV cache. Match them to the package tier (below);
  `launch.sh` warns if you don't.
- Preview a prompt without running anything: append `--dry-run`.
- Headless agents print nothing until they exit. `--stream` shows each tool call
  as it happens; it is on by default for `claude-local`, `--no-stream` opts out.
- Each run creates a `wp/<id>` worktree under a sibling `*-agent-trees/` dir,
  pre-seeds the env if `agentwork/scripts/prepare-worktree.sh` exists, runs the
  agent headless, and prints its branch status + report path.

## Package tiers

Each package declares a tier on its second line, which decides both the brief it
receives and which agent should run it. A missing tier means `code`.

| Tier | Brief given | Run on | Produces |
|---|---|---|---|
| `plan` | `CONTEXT.md` | `claude` / `gemini` / `codex` | specs, ADRs, WP splits |
| `code` | `CONTEXT.md` | hosted, `claude-local`, `pi-code` | implementation |
| `mini-code` | `CONTEXT-lite.md` | `pi-code` | small, fully closed implementation |

`standard` and `small` are the previous names for `code` and `mini-code`, still
accepted so existing packages keep working.

The taxonomy names **which brain does which work**. It applies equally when you
are at the keyboard (Claude Code vs `pi-local`) and when
`launch.sh` runs a package headless.

What defeats a small model is **open design decisions**, not length — so a
`mini-code` package is not merely a shorter one. Its decisions are already made:
exact signatures and data shapes written into the spec, tests supplied rather
than described, one or two owned files, nothing left to invent. A big package
cut into three equally open pieces is still three packages a small model cannot
finish. `plan` packages may explore open decisions within a specified subtree;
their proposals still require human acceptance before implementation.

`CONTEXT-lite.md` exists for the same reason: the full brief carries history,
rationale and platform gotchas that help a large model judge well and merely
crowd the window of a small one.

### Splitting an oversized package

```sh
agentwork/scripts/split.sh WP09 claude --target pi-code   # --dry-run to preview
agentwork/scripts/split.sh WP09 claude --target hosted
```

**Establish `--target` first** (see Phase 1), reusing an explicit session choice.
It decides whether children need shipped stubs or may retain FREE engineering choices.

Delegates the decomposition to a capable agent — it is a judgement call, not a
text operation, so `split.sh` refuses to run on a local model. It writes
`WP09a-*.md`, `WP09b-*.md`, … plus `WP09-SPLIT.md` explaining the division, and
**leaves `WP09` untouched** so you can still run it whole on a hosted agent.
Review the specs before merging: ownership really disjoint, shared types created
by the first package, no unresolved semantics required for implementation. Then
merge and launch the children. Do not resolve OPEN/DEFERRED questions merely to
make a package closed; return those blockers to human steering.

## Phase 4: Review, then merge (orchestrator only)

For each finished package, **you** do what the sandboxed worker could not:

1. Read the branch diff (`git diff <base>..wp/<id>`) and the worker's
   `agentwork/reports/wp<nn>.md`. Compare against the accepted design, not inferred
   intent from the code; classify findings using the design reference. Check new
   behavior and test expectations for node provenance, including claimed FREE choices.
2. Run the checks — tests, `node --check`, `bash -n`, and the **live / browser /
   Docker** checks the sandbox can't reach.
3. Validate findings and fix named IDs within the accepted scope or send them
   back for targeted repair. New semantics require human steering, not reviewer
   invention. Do not merge unresolved semantic blockers on the strength of tests.
4. **Merge in dependency order** (`git merge --no-ff wp/<id>`), then remove the
   worktree (`git worktree remove …`). Rebuild/re-verify the integrated result.

## Hard rules

- **Workers never push.** They commit locally in their worktree; the
  **orchestrator** integrates. Follow `.ai/git.md` for pushes (ask first).
- Each worker touches **only** the files its package declares — this is what
  keeps parallel runs from colliding.
- `agentwork/logs/` (raw prompts + transcripts) is **gitignored** — never commit
  it, and don't publish it.
- A passing worker report is not acceptance; **your review + live checks are**.

## Kickoff phrase

The user invokes this with **"use the parallel-agents workflow"** (or "delegate
this to parallel agents"). On that, read this file, then start at Phase 1.
