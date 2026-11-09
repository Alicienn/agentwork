"""Python (pytest/unittest) anti-cheat checks — the original agentwork AST rules,
plus detection of removed tests."""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence, Set

from .rust_checks import git_show


def is_python_test_path(rel: str) -> bool:
    if not rel.endswith(".py"):
        return False
    p = PurePosixPath(rel)
    return rel.startswith("tests/") or bool(re.match(r"^(test_.*|.*_test)\.py$", p.name.lower()))


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node) if hasattr(ast, "unparse") else ""


def check_python_source(source: str, label: str) -> List[str]:
    """Return gate failure messages for one test file (empty list = clean)."""
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as e:
        return [f"Failed to parse Python test file '{label}': {e}"]
    errors: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_str = _unparse(node.func)
            if func_str in ("pytest.skip", "unittest.skip", "pytest.xfail", "self.skipTest"):
                errors.append(f"Test file '{label}' calls {func_str}().")
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "pytestmark":
                    val_str = _unparse(node.value)
                    if "skip" in val_str or "xfail" in val_str:
                        errors.append(f"Test file '{label}' sets pytestmark skip/xfail ({val_str}).")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            stmts = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if not stmts or (len(stmts) == 1 and isinstance(stmts[0], ast.Pass)) or \
                    (len(stmts) == 1 and isinstance(stmts[0], ast.Expr) and isinstance(stmts[0].value, ast.Constant)
                     and stmts[0].value.value is Ellipsis):
                errors.append(f"Test function '{node.name}' in {label} is an empty stub.")
            for dec in node.decorator_list:
                dec_target = dec.func if isinstance(dec, ast.Call) else dec
                dec_str = _unparse(dec_target)
                if re.search(r"\b(skip|skipif|xfail|skipIf|skipUnless|expectedFailure)\b", dec_str):
                    errors.append(f"Test function '{node.name}' in {label} uses a skip/xfail decorator ({dec_str}).")
    return errors


def test_names(source: Optional[str]) -> Set[str]:
    if not source:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: Set[str] = set()

    def walk(body, prefix: str) -> None:
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                names.add(prefix + n.name)
            elif isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".")

    walk(tree.body, "")
    return names


def check_python_changes(target: Path, base: str, head: str, changed: Sequence[str],
                         forbid_test_removal: bool = True) -> List[str]:
    errors: List[str] = []
    before: Set[str] = set()
    after: Set[str] = set()
    for rel in changed:
        if not is_python_test_path(rel):
            continue
        new = git_show(target, head, rel)
        if new is not None:
            errors.extend(check_python_source(new, str(target / rel)))
        if forbid_test_removal:
            before.update(test_names(git_show(target, base, rel)))
            after.update(test_names(new))
    removed = sorted(before - after)
    if removed:
        errors.append("Python tests removed or renamed (verification.forbid_test_removal): " + ", ".join(removed))
    return errors
