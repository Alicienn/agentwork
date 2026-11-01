# Unified Agent Template (Exploration + Autonomous Pipeline)

A portable, deterministic multi-agent engineering workflow for AI coding agents (`claude`, `claude-or`, `codex`, `agy`, `claude-local`, `pi-code`). Supports both **interactive epistemic design exploration** (`.ai/workflows/design-exploration.md`) and **fully autonomous multi-agent execution** (`agentwork/pipeline.py drive`).

## Core Modes

1. **Interactive / Exploration Mode (Default)**:
   - Reads `AGENTS.md` and `.ai/session.md`.
   - Uses `.ai/workflows/design-exploration.md` to map `AXIOM`, `CHOICE`, `ASSUMPTION`, and `QUESTION` nodes (`FIXED [F]`, `FREE [*]`, `OPEN [?]`, `DEFERRED [D]`, `REJECTED [X]`) and let the human steer before freezing `docs/design/DESIGN.md`.
   - Also supports `.ai/workflows/big-work.md` (IETF drafts, research, long-form writing) and `.ai/workflows/plan-then-execute.md`.
2. **Autonomous Pipeline Mode (Phases 1–4)**:
   - **Interchangeable Driver**: Phases 1, 2, and 4 can be driven by Claude Code, Codex, or Antigravity (`agy`) following `Bootstrap an Autonomous Multi-Agent Repository.md`.
   - **Controller-First Execution**: Phase 3 (Build) is executed deterministically by `python3 agentwork/pipeline.py drive`, which manages worktrees, gates, retries, and integration.
   - **Privacy Profiles**:
     - `openrouter-first`: Leans on free models (`claude-or:nemotron_ultra`, `nemotron_super`) and local models, with paid fallback on quota/failure.
     - `sensitive`: Hard-blocks `claude-or` and third-party routers; allows only approved direct subscription endpoints (`claude`, `codex`, `agy`) and local models.
     - `balanced`: Multi-vendor subscription CLIs and local models.
     - `local-only`: Airgapped local Ollama only.
   - **Worktree-Aware Seatbelt Isolation**: Workers run in dedicated git worktrees (`../<repo>-agent-trees/<ID>`) with narrowed file-write permissions enforced by Darwin Seatbelt and Layer-1 mechanical gates (`verify_gate.sh`).

## Quickstart

### 1. Install or Update a Project
```bash
./setup.sh /path/to/project --profile openrouter-first   # or --profile sensitive | balanced | local-only
./setup.sh /path/to/project --dry-run                    # preview updates without writing
```

### 2. Run Interactive / Design Exploration
> Read `AGENTS.md`, then load `.ai/session.md` and propose the next step.

### 3. Run Autonomous Multi-Agent Build
> Implement `<Project Goal>` following `Bootstrap an Autonomous Multi-Agent Repository.md`

Or run the headless autopilot directly once sealed:
```bash
cd /path/to/project
./agentwork/scripts/driver.sh --build-all
```
