"""Dependency-free reader for agentwork/config/project.yaml.

Supports the YAML subset the harness uses: nested mappings by indentation,
block lists (``- item``), inline lists (``[a, b]``), quoted/unquoted scalars,
booleans, integers, floats and ``#`` comments. Anything fancier (anchors,
multi-line strings, flow mappings) is out of scope on purpose.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Mini YAML
# --------------------------------------------------------------------------- #


def _strip_comment(line: str) -> str:
    out = []
    quote: Optional[str] = None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline_list(text: str) -> List[str]:
    items, cur, quote = [], [], None
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        items.append("".join(cur))
    return [i.strip() for i in items if i.strip()]


def parse_scalar(text: str) -> Any:
    t = text.strip()
    if t == "":
        return ""
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        inner = t[1:-1]
        if t[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    if t.startswith("[") and t.endswith("]"):
        return [parse_scalar(x) for x in _split_inline_list(t[1:-1])]
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?", t):
        return float(t)
    return t


def load_yaml(text: str) -> Dict[str, Any]:
    lines: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        s = _strip_comment(raw)
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        lines.append((indent, s.strip()))

    pos = 0

    def parse_block(indent: int) -> Any:
        nonlocal pos
        if pos < len(lines) and lines[pos][1].startswith("- ") or (pos < len(lines) and lines[pos][1] == "-"):
            result_list: List[Any] = []
            while pos < len(lines) and lines[pos][0] == indent and (lines[pos][1].startswith("- ") or lines[pos][1] == "-"):
                item = lines[pos][1][1:].strip()
                pos += 1
                if item == "" and pos < len(lines) and lines[pos][0] > indent:
                    result_list.append(parse_block(lines[pos][0]))
                else:
                    result_list.append(parse_scalar(item))
            return result_list
        result: Dict[str, Any] = {}
        while pos < len(lines) and lines[pos][0] == indent:
            content = lines[pos][1]
            m = re.match(r"^([^:]+?):(?:\s+(.*))?$", content)
            if not m:
                raise ValueError(f"Unsupported YAML line: {content!r}")
            key = m.group(1).strip().strip('"').strip("'")
            val = m.group(2) or ""
            pos += 1
            if val.strip() == "":
                if pos < len(lines) and lines[pos][0] > indent:
                    result[key] = parse_block(lines[pos][0])
                elif pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
                    # YAML allows "key:\n- a\n- b" at the same indentation.
                    result[key] = parse_block(indent)
                else:
                    result[key] = None
            else:
                result[key] = parse_scalar(val)
        return result

    if not lines:
        return {}
    data = parse_block(lines[0][0])
    if pos != len(lines):
        raise ValueError(f"Could not parse YAML near: {lines[pos][1]!r}")
    if not isinstance(data, dict):
        raise ValueError("Top-level YAML document must be a mapping")
    return data


# --------------------------------------------------------------------------- #
# Project configuration with defaults
# --------------------------------------------------------------------------- #

RUST_GUARDED_FILES = [
    "Cargo.toml",
    "**/Cargo.toml",
    "Cargo.lock",
    "**/Cargo.lock",
    "build.rs",
    "**/build.rs",
    "clippy.toml",
    ".clippy.toml",
    "**/clippy.toml",
    "**/.clippy.toml",
    "rustfmt.toml",
    ".rustfmt.toml",
    "**/rustfmt.toml",
    "**/.rustfmt.toml",
    "rust-toolchain",
    "rust-toolchain.toml",
    ".cargo/**",
    "**/.cargo/**",
    "deny.toml",
]

DEFAULTS: Dict[str, Any] = {
    "project": {
        "name": "<PROJECT>",
        "version": "0.1.0",
        "repo_id": "",
        "routing_profile": "openrouter-first",
        "isolation_mode": "auto",
        "claude_sandbox": False,
        "default_driver": "auto",
        "max_retries_per_rung": 2,
        "max_total_attempts_per_wp": 6,
        "global_timeout_seconds": 14400,
        "worker_timeout_seconds": 1800,
        "gate_timeout_seconds": 300,
    },
    "protected_files": [],
    "languages": "auto",
    "verification": {
        "test_command": "",
        "lint_command": "",
        "shell": "auto",
        "run_project_checks_per_task": False,
        "forbid_test_removal": True,
    },
    "python": {
        "test_command": "",
        "lint_command": "",
    },
    "rust": {
        "test_command": "",
        "fmt_command": "cargo fmt --all -- --check",
        "clippy_command": "cargo clippy --all-targets -- -D warnings",
        "checker": "auto",
        "forbid_new_crate_allows": True,
        "extra_assertion_names": [],
        "guarded_files": list(RUST_GUARDED_FILES),
        "mutants": {
            "enabled": False,
            "min_kill_ratio": 0.8,
            "count_timeouts_as_caught": True,
            "timeout_seconds": 3600,
            "extra_args": "",
        },
    },
}

VALID_PROFILES = ("openrouter-first", "sensitive", "balanced", "local-only")
VALID_ISOLATION = ("auto", "worktree-seatbelt", "worktree-only")
KNOWN_LANGUAGES = ("python", "rust")


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is None and isinstance(out.get(k), (list, dict)):
            continue
        else:
            out[k] = v
    return out


def load_project_config(path: Path) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    if path.exists():
        raw = load_yaml(path.read_text(encoding="utf-8"))
    cfg = _deep_merge(DEFAULTS, raw)
    if cfg.get("protected_files") is None:
        cfg["protected_files"] = []
    return cfg


def detect_languages(repo: Path) -> List[str]:
    langs: List[str] = []
    if any((repo / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        langs.append("python")
    if (repo / "Cargo.toml").exists():
        langs.append("rust")
    return langs


def enabled_languages(cfg: Dict[str, Any], repo: Path) -> List[str]:
    """Languages whose checks are active. ``languages: auto`` = detect from files.

    The Python AST test checks have always run on any changed ``*.py`` test file,
    so Python stays enabled when nothing else is detected (backwards compatible).
    """
    val = cfg.get("languages", "auto")
    if isinstance(val, str):
        if val.strip().lower() == "auto":
            langs = detect_languages(repo)
            return langs or ["python"]
        val = [v.strip() for v in val.split(",") if v.strip()]
    langs = [str(v).strip().lower() for v in (val or [])]
    unknown = [l for l in langs if l not in KNOWN_LANGUAGES]
    if unknown:
        raise ValueError(f"Unknown language(s) in project.yaml 'languages': {unknown}; known: {list(KNOWN_LANGUAGES)}")
    return langs


def flat_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy flat dict used by pipeline.py (keys of the historical regex parser)."""
    p = cfg["project"]
    v = cfg["verification"]
    return {
        "routing_profile": str(p.get("routing_profile") or ""),
        "isolation_mode": str(p.get("isolation_mode") or "auto"),
        "max_retries_per_rung": int(p.get("max_retries_per_rung", 2)),
        "max_total_attempts_per_wp": int(p.get("max_total_attempts_per_wp", 6)),
        "global_timeout_seconds": int(p.get("global_timeout_seconds", 14400)),
        "worker_timeout_seconds": int(p.get("worker_timeout_seconds", 1800)),
        "gate_timeout_seconds": int(p.get("gate_timeout_seconds", 300)),
        "test_command": str(v.get("test_command") or ""),
        "lint_command": str(v.get("lint_command") or ""),
        "default_driver": str(p.get("default_driver") or "auto"),
        "repo_id": str(p.get("repo_id") or ""),
    }


def set_scalar_in_yaml_text(text: str, key: str, value: str) -> str:
    """Replace the first ``key: ...`` line, keeping indentation and trailing comment."""
    pat = re.compile(rf"^(\s*{re.escape(key)}:)[ \t]*([^#\n]*?)([ \t]+#.*)?$", re.MULTILINE)
    return pat.sub(lambda m: f'{m.group(1)} "{value}"{m.group(3) or ""}', text, count=1)
