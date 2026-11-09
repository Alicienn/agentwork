# agentwork — parallel coding-agent harness

Run several coding agents (`claude` / `claude-local` / `pi-code` / `gemini` / `codex`) against Markdown
**work-package** specs, each in its own isolated git worktree. The orchestrating
agent (or you) writes the specs, launches the workers, then reviews and merges.
Full method: `.ai/workflows/parallel-agents.md`.

For design-driven work, packages include the accepted design revision, relevant
constraints, delegated engineering choices, and behavior/test provenance. See
[design exploration](../.ai/workflows/design-exploration.md). Independent review
classifies findings and requests targeted repairs before integration. Agy may
participate through a manual handoff; the launcher supports only its listed agents.
Existing projects retain their seeded CONTEXT briefs on upgrade: put required
contract excerpts in packages, including those using the lite brief.

```
agentwork/
  CONTEXT.md                  shared brief, prepended to every WP prompt
  packages/WP<NN>-*.md        one spec per unit of work (WP-template.md = skeleton)
  CONTEXT-lite.md             shorter brief, given to Tier: mini-code packages
  pipeline.py                 all logic: run/drive/merge/seal + gate, launch, split, driver, setup
  awlib/                      portable Python modules behind pipeline.py (no dependencies)
  tools/rust-testcheck/       syn-based Rust test checker used by the gate
  scripts/launch.sh|.cmd      launch <WP> <agent> [--dry-run]   (= pipeline.py launch)
  scripts/split.sh|.cmd       split an oversized WP into Tier: mini-code children (= pipeline.py split)
  scripts/verify_gate.sh|.cmd mechanical gate (= pipeline.py gate)
  scripts/prepare-worktree.sh optional per-project env pre-seed (see .example)
  reports/wp<nn>.md           each worker writes its result here
  logs/                       prompts + transcripts (gitignored)
```

## Use it

```sh
# 1. Fill in CONTEXT.md for this project (once).
# 2. Write one package per unit of work; make packages own DISJOINT files.
cp agentwork/packages/WP-template.md agentwork/packages/WP01-<slug>.md   # edit it

# 3. Launch — in parallel, one agent per package:
agentwork/scripts/launch.sh WP01 codex        &
agentwork/scripts/launch.sh WP02 gemini       &
agentwork/scripts/launch.sh WP03 claude       &
agentwork/scripts/launch.sh WP04 pi-code      &   # local model, no API credits
wait

# 4. Review each worktree's diff, run the checks the sandbox couldn't,
#    then merge (the orchestrator merges — workers never push).
```

On Windows use `agentwork\scripts\launch.cmd WP01 codex` (or `py -3 agentwork\pipeline.py launch WP01 codex`)
in separate terminals. Windows has no OS sandbox: see the root README, "Windows: degraded isolation".

Preview an assembled prompt without running anything:
`agentwork/scripts/launch.sh WP01 claude --dry-run`.

## Rules

- Workers commit locally and **never push**; the orchestrator integrates.
- Each worker touches only the files its package declares (so parallel runs
  don't collide) and writes `reports/wp<nn>.md` when done.
- `agentwork/logs/` holds raw prompts and transcripts — **gitignored**, never
  committed.
- Run **at most one** `claude-local` worker at a time: Ollama serves a single
  model instance, so concurrent local workers queue behind each other and each
  parallel slot costs another KV cache (~6 GB at 64k).
