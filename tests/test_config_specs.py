from __future__ import annotations

import pytest

from awlib import config as c
from awlib import specs


def test_yaml_nested_lists_scalars_and_comments():
    data = c.load_yaml("""
# top comment
project:
  name: "demo"   # trailing comment
  count: 3
  ratio: 0.75
  on: true
  url: "http://x/#not-a-comment"
  empty: ""
list_block:
  - "a"
  - b
inline: [x, "y z", 3]
nested:
  deeper:
    key: value: with colon
    lst:
    - one
""")
    assert data["project"] == {"name": "demo", "count": 3, "ratio": 0.75, "on": True,
                               "url": "http://x/#not-a-comment", "empty": ""}
    assert data["list_block"] == ["a", "b"]
    assert data["inline"] == ["x", "y z", 3]
    assert data["nested"]["deeper"]["key"] == "value: with colon"
    assert data["nested"]["deeper"]["lst"] == ["one"]


def test_template_project_yaml_parses_with_expected_defaults(tmp_path):
    from conftest import TEMPLATE

    cfg = c.load_project_config(TEMPLATE / "agentwork" / "config" / "project.yaml")
    assert cfg["project"]["isolation_mode"] == "auto"
    assert cfg["project"]["claude_sandbox"] is False
    assert cfg["rust"]["mutants"]["enabled"] is False
    assert cfg["rust"]["mutants"]["min_kill_ratio"] == 0.8
    assert "Cargo.toml" in cfg["rust"]["guarded_files"]
    assert cfg["rust"]["clippy_command"] == "cargo clippy --all-targets -- -D warnings"
    flat = c.flat_view(cfg)
    assert flat["gate_timeout_seconds"] == 300


def test_missing_config_gives_defaults(tmp_path):
    cfg = c.load_project_config(tmp_path / "nope.yaml")
    assert cfg["languages"] == "auto"
    assert cfg["verification"]["forbid_test_removal"] is True


def test_language_detection_and_override(tmp_path):
    assert c.enabled_languages({"languages": "auto"}, tmp_path) == ["python"]  # backwards compatible default
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert c.enabled_languages({"languages": "auto"}, tmp_path) == ["rust"]
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert c.enabled_languages({"languages": "auto"}, tmp_path) == ["python", "rust"]
    assert c.enabled_languages({"languages": ["rust"]}, tmp_path) == ["rust"]
    assert c.enabled_languages({"languages": "python, rust"}, tmp_path) == ["python", "rust"]
    with pytest.raises(ValueError):
        c.enabled_languages({"languages": ["cobol"]}, tmp_path)


def test_set_scalar_keeps_comment():
    text = 'project:\n  routing_profile: "<REQUIRED>"  # pick one\n  isolation_mode: "auto"\n'
    out = c.set_scalar_in_yaml_text(text, "routing_profile", "balanced")
    assert 'routing_profile: "balanced"  # pick one' in out
    assert c.load_yaml(out)["project"]["routing_profile"] == "balanced"


@pytest.mark.parametrize("pat, path, ok", [
    ("src/*.rs", "src/lib.rs", True),
    ("src/*.rs", "src/a/b.rs", False),
    ("src/**", "src/a/b.rs", True),
    ("src/**", "src", True),
    ("**/Cargo.toml", "Cargo.toml", True),
    ("**/Cargo.toml", "crates/x/Cargo.toml", True),
    ("./tests/test_?.py", "tests/test_a.py", True),
    ("tests\\acceptance.rs", "tests/acceptance.rs", True),
    (".cargo/**", ".cargo/config.toml", True),
    ("docs/design/DESIGN.md", "docs/design/DESIGN.mdx", False),
])
def test_globs(pat, path, ok):
    assert specs.matches_any(path, [pat]) is ok


def test_frontmatter_crlf_and_owns_fallback():
    content = "---\r\nid: WP07\r\nowns:\r\n  - src/a.rs\r\nlocked_tests: [tests/x.rs]\r\nrung: 2\r\n---\r\n# t\r\nVerify: cargo test\r\n"
    meta, body = specs.parse_frontmatter(content)
    assert meta["id"] == "WP07" and meta["owns"] == ["src/a.rs"] and meta["locked_tests"] == ["tests/x.rs"]
    assert meta["rung"] == 2
    assert specs.verify_line(content) == "cargo test"
    meta2, _ = specs.parse_frontmatter("# x\n\n## Owns\n\n- `src/b.rs` — thing\n\n## Other\n- `nope`\n")
    assert meta2["owns"] == ["src/b.rs"]


def test_find_task_spec(tmp_path):
    d = tmp_path / "packages"
    d.mkdir()
    for n in ("WP-template.md", "WP01-SPLIT.md", "WP01-thing.md", "wp02.md"):
        (d / n).write_text("x", encoding="utf-8")
    assert specs.find_task_spec("wp01", [d]).name == "WP01-thing.md"
    assert specs.find_task_spec("WP02", [d]).name == "wp02.md"
    assert specs.find_task_spec("WP03", [d]) is None
