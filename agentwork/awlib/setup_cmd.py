"""Install or UPDATE the agentwork template in a project (portable setup.sh / update-all.sh).

    pipeline.py setup [TARGET] [--profile P] [--isolation M] [--lock-push] [--dry-run] [--force]
    pipeline.py update-all [--dry-run] [--force] [--roots DIR[:DIR...]]

MANAGED files are harness logic owned by the template — updated in place if the
local copy is unmodified (its hash matches any historical template version).
SEEDED files are project-specific — created once, then never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Sequence, Set

from . import config as awconfig
from .oscompat import IS_WINDOWS, normalize_newlines

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent

MANAGED_STATIC = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "Bootstrap an Autonomous Multi-Agent Repository.md",
    ".claude/settings.json",
    ".codex/config.toml",
    ".agents/rules/autonomous-driver.md",
    ".ai/index.md",
    ".ai/git.md",
    ".ai/workflows/_contract.md",
    ".ai/workflows/big-work.md",
    ".ai/workflows/plan-then-execute.md",
    ".ai/workflows/parallel-agents.md",
    ".ai/workflows/design-exploration.md",
    ".ai/workflows/design-example.md",
    "agentwork/README.md",
    "agentwork/pipeline.py",
    "agentwork/tui.py",
    "agentwork/packages/WP-template.md",
    "agentwork/tools/rust-testcheck/Cargo.toml",
    "agentwork/tools/rust-testcheck/Cargo.lock",
    "agentwork/tools/rust-testcheck/src/main.rs",
]
MANAGED_GLOBS = ["agentwork/awlib/*.py", "agentwork/scripts/*.sh", "agentwork/scripts/*.cmd",
                 "agentwork/scripts/prepare-worktree.sh.example"]

SEEDED = [
    ".ai/session.md",
    ".ai/commands.md",
    ".ai/decisions.md",
    "agentwork/CONTEXT.md",
    "agentwork/CONTEXT-lite.md",
    "agentwork/config/project.yaml",
    "agentwork/config/agents.yaml",
    "agentwork/state/PROJECT.md",
    "agentwork/state/DECISIONS.md",
    "agentwork/state/QUESTIONS.md",
]


def managed_files(template: Path = TEMPLATE_DIR) -> List[str]:
    out = list(MANAGED_STATIC)
    for g in MANAGED_GLOBS:
        for p in sorted(template.glob(g)):
            if p.is_file():
                rel = p.relative_to(template).as_posix()
                if rel not in out:
                    out.append(rel)
    return out


def file_hash(path: Path) -> str:
    return hashlib.sha256(normalize_newlines(path.read_bytes())).hexdigest()


def known_hashes(rel: str, src: Path, template: Path) -> Set[str]:
    hashes: Set[str] = set()
    for p in (src, template / rel):
        if p.is_file():
            hashes.add(file_hash(p))
    if rel == ".claude/settings.json" and (template / ".claude/settings.sensitive.json").is_file():
        hashes.add(file_hash(template / ".claude/settings.sensitive.json"))
    if subprocess.run(["git", "-C", str(template), "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode != 0:
        return hashes
    revs = subprocess.run(["git", "-C", str(template), "log", "--all", "--format=%H", "--", rel],
                          capture_output=True, text=True).stdout.split()
    for c in revs:
        r = subprocess.run(["git", "-C", str(template), "show", f"{c}:{rel}"], capture_output=True)
        if r.returncode == 0:
            hashes.add(hashlib.sha256(normalize_newlines(r.stdout)).hexdigest())
    return hashes


class Setup:
    def __init__(self, target: Path, profile: str, isolation: str, dry: bool, force: bool, template: Path = TEMPLATE_DIR):
        self.target, self.profile, self.isolation = target, profile, isolation
        self.dry, self.force, self.template = dry, force, template
        self.n_create = self.n_update = self.n_skip = self.n_custom = 0
        self.custom: List[str] = []

    def say(self, msg: str) -> None:
        print(("would " + msg) if self.dry else msg)

    def _copy(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        if dest.suffix == ".sh" and not IS_WINDOWS:
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def install_managed(self, rel: str) -> None:
        src = self.template / rel
        if rel == ".claude/settings.json" and self.profile == "sensitive" and (self.template / ".claude/settings.sensitive.json").is_file():
            src = self.template / ".claude/settings.sensitive.json"
        dest = self.target / rel
        if not src.is_file():
            return
        if not dest.exists():
            self.say(f"create: {rel}")
            if not self.dry:
                self._copy(src, dest)
            self.n_create += 1
            return
        local = file_hash(dest)
        if local == file_hash(src):
            self.n_skip += 1
            return
        if local in known_hashes(rel, src, self.template):
            self.say(f"update: {rel}")
            if not self.dry:
                self._copy(src, dest)
            self.n_update += 1
        elif self.force:
            self.say(f"FORCE update: {rel} (backup -> {rel}.bak)")
            if not self.dry:
                shutil.copyfile(dest, dest.with_name(dest.name + ".bak"))
                self._copy(src, dest)
            self.n_update += 1
        else:
            print(f"KEEP:   {rel} (locally modified — use --force to overwrite)")
            self.custom.append(rel)
            self.n_custom += 1

    def install_seeded(self, rel: str) -> None:
        src, dest = self.template / rel, self.target / rel
        if not src.is_file():
            return
        if dest.exists():
            self.n_skip += 1
            return
        self.say(f"create: {rel}")
        if not self.dry:
            self._copy(src, dest)
        self.n_create += 1

    def merge_fragment(self, fragment: str, dest_name: str) -> None:
        frag = self.template / fragment
        if not frag.is_file() or not self.target.is_dir():
            return
        dest = self.target / dest_name
        lines = [l for l in frag.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        if dest.is_file():
            existing = set(dest.read_text(encoding="utf-8").splitlines())
            missing = [l for l in lines if l not in existing]
            for l in missing:
                self.say(f"{dest_name}: +{l}")
            if missing and not self.dry:
                with open(dest, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write("\n" + "\n".join(missing) + "\n")
        else:
            self.say(f"create: {dest_name}")
            if not self.dry:
                shutil.copyfile(frag, dest)


def run_setup(target: Path, profile: str = "", isolation: str = "", lock_push: bool = False, dry: bool = False,
              force: bool = False, template: Path = TEMPLATE_DIR) -> int:
    if not target.is_dir():
        if dry:
            print(f"would create target directory: {target}")
        else:
            target.mkdir(parents=True)
    target = target.resolve() if target.exists() else target
    cfg_path = target / "agentwork" / "config" / "project.yaml"
    profile_explicit, isolation_explicit = bool(profile), bool(isolation)
    existing = {}
    if cfg_path.is_file():
        try:
            existing = awconfig.load_yaml(cfg_path.read_text(encoding="utf-8")).get("project", {}) or {}
        except ValueError:
            existing = {}
    if not profile and existing.get("routing_profile") not in (None, "", "<REQUIRED>"):
        profile = str(existing["routing_profile"])
    if not isolation and existing.get("isolation_mode"):
        isolation = str(existing["isolation_mode"])
    isolation = isolation or "auto"
    if not profile:
        print("ERROR: --profile is required on initial setup.", file=sys.stderr)
        print("Choices: " + " | ".join(awconfig.VALID_PROFILES), file=sys.stderr)
        return 1
    if profile not in awconfig.VALID_PROFILES:
        print(f"ERROR: Invalid profile '{profile}'. Must be: {' | '.join(awconfig.VALID_PROFILES)}", file=sys.stderr)
        return 1
    if isolation not in awconfig.VALID_ISOLATION:
        print(f"ERROR: Invalid --isolation '{isolation}'. Must be: {' | '.join(awconfig.VALID_ISOLATION)}", file=sys.stderr)
        return 1

    s = Setup(target, profile, isolation, dry, force, template)
    print(f"target: {target} (Profile: {profile}, Isolation: {isolation})")
    if dry:
        print("(dry run — nothing will be written)")
    else:
        for sub in ("notes", "scratch", "plans", "drafts", "workflows"):
            (target / ".ai" / sub).mkdir(parents=True, exist_ok=True)
        for sub in ("notes", "scratch", "plans", "drafts"):
            (target / ".ai" / sub / ".gitkeep").touch()
        ai_link = target / "ai"
        if not ai_link.exists() and not ai_link.is_symlink():
            try:
                os.symlink(".ai", ai_link, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass  # symlinks need Developer Mode on Windows; the 'ai' alias is optional
        for sub in ("config", "scripts", "packages", "reports", "state", "logs"):
            (target / "agentwork" / sub).mkdir(parents=True, exist_ok=True)
        (target / "agentwork" / "reports" / ".gitkeep").touch()
        for sub in (".claude", ".codex", ".agents/rules"):
            (target / sub).mkdir(parents=True, exist_ok=True)

    new_config = not cfg_path.exists()
    for rel in managed_files(template):
        s.install_managed(rel)
    for rel in SEEDED:
        s.install_seeded(rel)

    if not dry and cfg_path.is_file():
        text = cfg_path.read_text(encoding="utf-8")
        data = awconfig.load_yaml(text).get("project", {}) or {}
        if not str(data.get("repo_id") or "").strip():
            text = awconfig.set_scalar_in_yaml_text(text, "repo_id", str(uuid.uuid4()))
        if new_config or profile_explicit:
            text = awconfig.set_scalar_in_yaml_text(text, "routing_profile", profile)
        if new_config or isolation_explicit:
            text = awconfig.set_scalar_in_yaml_text(text, "isolation_mode", isolation)
        with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    s.merge_fragment(".gitignore.fragment", ".gitignore")
    s.merge_fragment(".gitattributes.fragment", ".gitattributes")

    if not dry and shutil.which("git"):
        if subprocess.run(["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode != 0:
            if os.environ.get("GIT_INIT", "1") == "0":
                print("skip:   git init (GIT_INIT=0)")
            else:
                subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
                print("init:   git repository")
        if lock_push:
            subprocess.run(["git", "-C", str(target), "config", "remote.origin.pushurl", "no_push"], capture_output=True)

    print(f"result: {s.n_create} created, {s.n_update} updated, {s.n_skip} already current, {s.n_custom} kept (local edits)")
    if s.custom:
        print("        locally modified: " + " ".join(s.custom))
    if isolation == "auto" and IS_WINDOWS:
        print("NOTE: on Windows there is no OS sandbox; 'auto' isolation runs agents in worktree-only mode "
              "(see README, 'Windows: degraded isolation').")
    return 0


def setup_main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="setup", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=".")
    ap.add_argument("--profile", default="")
    ap.add_argument("--isolation", default="")
    ap.add_argument("--lock-push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ns = ap.parse_args(list(argv))
    return run_setup(Path(ns.target), ns.profile, ns.isolation, ns.lock_push, ns.dry_run, ns.force)


def find_projects(roots: Sequence[Path], template: Path = TEMPLATE_DIR) -> List[Path]:
    found: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "target") and not d.endswith("-agent-trees")
                           and not d.endswith("-trees")]
            if depth >= 5:
                dirnames[:] = []
            if Path(dirpath).name == "config" and "project.yaml" in filenames and Path(dirpath).parent.name == "agentwork":
                proj = Path(dirpath).parent.parent
                if (proj / ".ai").is_dir() and proj.resolve() != template.resolve():
                    found.append(proj)
    return sorted(set(found))


def update_all_main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="update-all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--roots", default=str(Path.home() / "Code"),
                    help=f"directories separated by '{os.pathsep}' or spaces")
    ns = ap.parse_args(list(argv))
    raw = ns.roots.replace(os.pathsep, " ")
    roots = [Path(r) for r in raw.split() if r]
    projects = find_projects(roots)
    if not projects:
        print("No template projects found under: " + " ".join(str(r) for r in roots))
        return 0
    print(f"Found {len(projects)} project(s) to update.")
    for p in projects:
        print(f"=== Updating {p} ===")
        try:
            if run_setup(p, dry=ns.dry_run, force=ns.force) != 0:
                print(f"WARNING: Failed updating {p}")
        except Exception as e:  # keep going with the other projects
            print(f"WARNING: Failed updating {p}: {e}")
    print("Update complete.")
    return 0
