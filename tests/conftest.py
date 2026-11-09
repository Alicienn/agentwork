"""Shared helpers for the agentwork harness test-suite."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(TEMPLATE / "agentwork"))

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check, env=env)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def commit_all(repo: Path, msg: str = "commit") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


class Project:
    """A target project with the harness installed, plus isolated state/trees dirs."""

    def __init__(self, root: Path, home: Path):
        self.root = root
        self.home = home
        self.env: Dict[str, str] = dict(os.environ)
        self.env.update(GIT_ENV)
        self.env.update({
            "AGENTWORK_STATE_HOME": str(home / "state"),
            "AGENT_TREES": str(home / "trees"),
            "PYTHONIOENCODING": "utf-8",
        })
        self.env.pop("AGENTWORK_DEFAULT_AGENT", None)

    @property
    def pipeline(self) -> Path:
        return self.root / "agentwork" / "pipeline.py"

    def run(self, *args: str, extra_env: Optional[Dict[str, str]] = None, timeout: float = 900) -> subprocess.CompletedProcess:
        env = dict(self.env)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(self.pipeline), *args], cwd=self.root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return git(self.root, *args, check=check)

    def commit(self, msg: str = "commit") -> str:
        return commit_all(self.root, msg)


def install_harness(target: Path, profile: str = "balanced", isolation: str = "auto") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(GIT_ENV)
    res = subprocess.run([sys.executable, str(TEMPLATE / "agentwork" / "pipeline.py"), "setup", str(target),
                          "--profile", profile, "--isolation", isolation],
                         capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    return res


@pytest.fixture
def make_project(tmp_path):
    """Factory: make_project(files={rel: text}, example=None) -> Project with harness installed and committed."""

    def factory(files: Optional[Dict[str, str]] = None, example: Optional[str] = None,
                profile: str = "balanced", project_yaml_patch=None) -> Project:
        root = tmp_path / "proj"
        if example:
            shutil.copytree(TEMPLATE / "examples" / example, root, ignore=shutil.ignore_patterns("target"))
        else:
            root.mkdir()
        for rel, text in (files or {}).items():
            write(root / rel, text)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "core.autocrlf", "false")
        install_harness(root, profile=profile)
        if project_yaml_patch:
            py = root / "agentwork" / "config" / "project.yaml"
            write(py, project_yaml_patch(py.read_text(encoding="utf-8")))
        proj = Project(root, tmp_path)
        proj.commit("initial")
        return proj

    return factory


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


requires_cargo = pytest.mark.skipif(not have("cargo"), reason="cargo not installed")
