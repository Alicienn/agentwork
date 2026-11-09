"""Interactive driver launcher / build supervisor (portable driver.sh).

    pipeline.py driver [claude|claude-or|codex|agy|gemini|claude-local|pi-code|auto] [--build-all] [extra args...]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

from . import config as awconfig
from .launch import repo_root
from .oscompat import IS_MACOS

DRIVERS = ("claude", "claude-or", "codex", "agy", "gemini", "claude-local", "pi-code", "auto")
KICKOFF_PROMPT = ("Read AGENTS.md, agentwork/state/PROJECT.md, and docs/design/DESIGN.md. "
                  "Then execute following Bootstrap an Autonomous Multi-Agent Repository.md.")


def pick_installed(cands: Sequence[str]) -> str:
    for c in cands:
        if shutil.which("pi" if c == "pi-code" else c):
            return c
    return cands[0]


def cli_main(argv: Sequence[str]) -> int:
    repo = repo_root()
    yaml_path = repo / "agentwork" / "config" / "project.yaml"
    if not yaml_path.exists():
        print(f"ERROR: {yaml_path} not found. Run setup first.", file=sys.stderr)
        return 1
    cfg = awconfig.load_project_config(yaml_path)
    profile = str(cfg["project"].get("routing_profile") or "")
    driver = str(cfg["project"].get("default_driver") or "auto")
    if not profile or profile == "<REQUIRED>":
        print(f"POLICY ERROR: routing_profile is not set in {yaml_path}.", file=sys.stderr)
        print("Specify a profile via: setup --profile <openrouter-first|sensitive|balanced|local-only>", file=sys.stderr)
        return 2

    build_all = False
    extra: List[str] = []
    for a in argv:
        if a == "--build-all":
            build_all = True
        elif a in DRIVERS:
            driver = a
        else:
            extra.append(a)

    if build_all:
        print(f"=== Starting Autonomous Build Supervisor (Profile: {profile}) ===")
        cmd = [sys.executable, str(repo / "agentwork" / "pipeline.py"), "drive"]
        if IS_MACOS and shutil.which("caffeinate"):
            cmd = ["caffeinate", "-dimsu"] + cmd
        print("Launching: " + " ".join(cmd), flush=True)
        return subprocess.call(cmd, cwd=repo)

    if driver == "auto":
        driver = {
            "openrouter-first": pick_installed(["claude-or", "claude", "codex", "agy", "gemini"]),
            "local-only": pick_installed(["claude-local", "pi-code"]),
        }.get(profile, pick_installed(["claude", "codex", "agy", "gemini"]))

    if profile == "sensitive" and (driver.startswith("claude-or") or "openrouter" in driver):
        print(f"SECURITY ERROR: '{driver}' is strictly forbidden under profile 'sensitive'.", file=sys.stderr)
        return 2
    if profile == "local-only" and driver not in ("claude-local", "pi-code"):
        print(f"SECURITY ERROR: Cloud driver '{driver}' is strictly forbidden under profile 'local-only'.", file=sys.stderr)
        return 2

    print(f"Launching Driver: {driver} (Profile: {profile})", flush=True)
    table = {
        "claude": ["claude", "--dangerously-skip-permissions", KICKOFF_PROMPT],
        "claude-or": ["claude-or", "--dangerously-skip-permissions", KICKOFF_PROMPT],
        "codex": ["codex", "--dangerously-bypass-approvals-and-sandbox", KICKOFF_PROMPT],
        "agy": ["agy", "--dangerously-skip-permissions", KICKOFF_PROMPT],
        "gemini": ["gemini", "-y", KICKOFF_PROMPT],
        "claude-local": ["claude-local", "--permission-mode", "bypassPermissions", KICKOFF_PROMPT],
        "pi-code": ["pi", "--provider", os.environ.get("PI_PROVIDER", "ollama"), "--model", "pi-code", KICKOFF_PROMPT],
    }
    if driver not in table:
        print(f"ERROR: Unknown driver '{driver}'", file=sys.stderr)
        return 1
    cmd = table[driver] + extra
    exe = shutil.which(cmd[0])
    if not exe:
        print(f"ERROR: '{cmd[0]}' not found in PATH", file=sys.stderr)
        return 127
    # os.exec* does not truly replace the process on Windows; a child + exit code works everywhere.
    return subprocess.call([exe] + cmd[1:], cwd=repo)
