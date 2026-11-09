"""Work-package spec helpers shared by pipeline, gate, launcher and splitter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SKIP_SUFFIXES = ("-template.md", "-SPLIT.md")


def _is_skipped(name: str) -> bool:
    return name.endswith(SKIP_SUFFIXES) or name.startswith("WP-template")


def find_task_spec(task_id: str, dirs: Iterable[Path]) -> Optional[Path]:
    """Find ``<ID>-*.md`` (or ``<ID>.md``) in the given directories, case-insensitive."""
    tid = task_id.upper()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if not f.is_file() or _is_skipped(f.name):
                continue
            prefix = f.name.split("-", 1)[0]
            if prefix.endswith(".md"):
                prefix = prefix[:-3]
            if prefix.upper() == tid or f.stem.upper() == tid:
                return f
    return None


def glob_to_regex(pat: str) -> "re.Pattern[str]":
    """Path glob -> regex: ``*`` stays within one segment, ``**`` crosses ``/``."""
    pat = pat.strip().replace("\\", "/")
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    if pat.endswith("/**"):
        base = re.escape(pat[:-3].rstrip("/"))
        return re.compile(rf"^{base}(/.*)?$")
    i, n = 0, len(pat)
    res = ["^"]
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # "**/" may match zero directories.
                if i + 2 < n and pat[i + 2] == "/":
                    res.append("(?:.*/)?")
                    i += 3
                    continue
                res.append(".*")
                i += 2
                continue
            res.append("[^/]*")
        elif c == "?":
            res.append("[^/]")
        else:
            res.append(re.escape(c))
        i += 1
    res.append("$")
    return re.compile("".join(res))


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    p = path.replace("\\", "/")
    return any(glob_to_regex(pat).match(p) for pat in patterns if pat)


def is_literal_pattern(pat: str) -> bool:
    return not any(c in pat for c in "*?[")


def normalize_rel(path: str) -> str:
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML-like frontmatter and the markdown ``## Owns`` fallback (no deps)."""
    content = content.replace("\r\n", "\n")
    meta: Dict[str, Any] = {}
    body = content
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if match:
        fm_text, body = match.group(1), match.group(2)
        current_list_key = None
        for line in fm_text.splitlines():
            line = line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            list_item_match = re.match(r"^\s*-\s+(.*)$", line)
            if list_item_match and current_list_key:
                val = list_item_match.group(1).strip().strip('"').strip("'")
                meta[current_list_key].append(val)
                continue
            key_val_match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
            if key_val_match:
                key, val = key_val_match.group(1), key_val_match.group(2).strip()
                if val == "":
                    meta[key] = []
                    current_list_key = key
                elif val.lower() == "true":
                    meta[key] = True
                    current_list_key = None
                elif val.lower() == "false":
                    meta[key] = False
                    current_list_key = None
                elif val.isdigit():
                    meta[key] = int(val)
                    current_list_key = None
                elif val.startswith("[") and val.endswith("]"):
                    meta[key] = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
                    current_list_key = None
                else:
                    meta[key] = val.strip('"').strip("'")
                    current_list_key = None

    for list_field in ("depends", "inputs", "outputs", "owns", "locked_tests", "done_when"):
        if list_field in meta:
            v = meta[list_field]
            if isinstance(v, str):
                meta[list_field] = [v] if v else []
            elif not isinstance(v, list):
                meta[list_field] = [str(v)]

    if not meta.get("owns"):
        md_owns: List[str] = []
        in_owns_md = False
        for line in body.splitlines():
            if re.match(r"^##\s+Owns", line):
                in_owns_md = True
                continue
            if in_owns_md:
                if line.startswith("## "):
                    in_owns_md = False
                elif line.strip().startswith("-"):
                    m = re.search(r"`([^`]+)`", line)
                    if m:
                        md_owns.append(m.group(1))
        if md_owns:
            meta["owns"] = md_owns

    return meta, body.strip()


def verify_line(body_or_content: str) -> Optional[str]:
    m = re.search(r"^Verify:[ \t]*(\S.*)$", body_or_content.replace("\r\n", "\n"), re.MULTILINE)
    return m.group(1).strip() if m else None


def tier_of(content: str) -> str:
    m = re.search(r"^Tier:[ \t]*([a-z-]*)", content.replace("\r\n", "\n"), re.MULTILINE)
    tier = (m.group(1) if m else "") or "code"
    return {"standard": "code", "small": "mini-code"}.get(tier, tier)
