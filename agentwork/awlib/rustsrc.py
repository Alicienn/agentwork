"""Pure-Python Rust source analysis (no regex on raw text).

A real lexer (nested block comments, raw strings, byte/C strings, char vs
lifetime, raw identifiers) builds token trees; a small item parser walks
modules, functions, impls and traits. Used for:

* ``builtin`` anti-cheat checks (fallback when the ``syn`` based helper in
  ``agentwork/tools/rust-testcheck`` cannot be built),
* public signature extraction for ``pipeline.py context``.

The syn helper and this module emit the same finding kinds; see
``awlib/rust_checks.py``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


class RustParseError(Exception):
    pass


@dataclass
class Tok:
    kind: str  # ident | punct | lit | lifetime | group
    text: str
    line: int
    start: int
    end: int
    delim: str = ""  # for groups: ( [ {
    children: List["Tok"] = field(default_factory=list)

    def is_punct(self, s: str) -> bool:
        return self.kind == "punct" and self.text == s

    def is_ident(self, s: Optional[str] = None) -> bool:
        return self.kind == "ident" and (s is None or self.text == s)

    def is_group(self, d: Optional[str] = None) -> bool:
        return self.kind == "group" and (d is None or self.delim == d)


_PUNCT3 = ("<<=", ">>=", "...", "..=")
_PUNCT2 = ("::", "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=",
           "^=", "&=", "|=", "<<", ">>", "..")
_STR_START = re.compile(r"(br|cr|r|b|c)?(#*)\"")
_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")", "]", "}"}


def lex(src: str) -> List[Tok]:
    """Return flat tokens (groups are built by :func:`tokenize`)."""
    out: List[Tok] = []
    i, n, line = 0, len(src), 1
    if src.startswith("﻿"):
        i = 1
    if src.startswith("#!", i) and not src.startswith("#![", i):
        # shebang line
        while i < n and src[i] != "\n":
            i += 1

    def err(msg: str) -> RustParseError:
        return RustParseError(f"line {line}: {msg}")

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth += 1
                    i += 2
                elif src.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    if src[i] == "\n":
                        line += 1
                    i += 1
            if depth:
                raise err("unterminated block comment")
            continue

        start, start_line = i, line

        # raw / byte / c string prefixes
        m = _STR_START.match(src, i) if c in "brc\"" else None
        if m is not None:
            prefix, hashes = m.group(1) or "", m.group(2)
            raw = prefix.endswith("r")
            if hashes and not raw:
                m = None
            if m is not None:
                i = m.end()
                if raw:
                    terminator = '"' + hashes
                    j = src.find(terminator, i)
                    if j < 0:
                        raise err("unterminated raw string")
                    line += src.count("\n", i, j)
                    i = j + len(terminator)
                else:
                    while i < n and src[i] != '"':
                        if src[i] == "\\":
                            i += 1
                        if i < n and src[i] == "\n":
                            line += 1
                        i += 1
                    if i >= n:
                        raise err("unterminated string")
                    i += 1
                # literal suffix
                while i < n and (src[i].isalnum() or src[i] == "_"):
                    i += 1
                out.append(Tok("lit", src[start:i], start_line, start, i))
                continue

        if c == "b" and i + 1 < n and src[i + 1] == "'":
            j = _scan_char(src, i + 1)
            if j is None:
                raise err("bad byte literal")
            i = j
            out.append(Tok("lit", src[start:i], start_line, start, i))
            continue

        if c == "'":
            j = _scan_char(src, i)
            if j is not None:
                i = j
                out.append(Tok("lit", src[start:i], start_line, start, i))
                continue
            # lifetime / label
            j = i + 1
            if src.startswith("r#", j):
                j += 2
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j == i + 1:
                raise err("stray quote")
            i = j
            out.append(Tok("lifetime", src[start:i], start_line, start, i))
            continue

        if c.isalpha() or c == "_" or ord(c) > 127:
            j = i
            if src.startswith("r#", i) and i + 2 < n and (src[i + 2].isalpha() or src[i + 2] == "_"):
                j = i + 2
            while j < n and (src[j].isalnum() or src[j] == "_" or ord(src[j]) > 127):
                j += 1
            text = src[i:j]
            if text.startswith("r#"):
                text = text[2:]
            i = j
            out.append(Tok("ident", text, start_line, start, i))
            continue

        if c.isdigit():
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            if j + 1 < n and src[j] == "." and src[j + 1].isdigit():
                j += 1
                while j < n and (src[j].isalnum() or src[j] == "_"):
                    j += 1
                if j < n and src[j - 1] in "eE" and src[j] in "+-":
                    j += 1
                    while j < n and (src[j].isalnum() or src[j] == "_"):
                        j += 1
            i = j
            out.append(Tok("lit", src[start:i], start_line, start, i))
            continue

        for p in _PUNCT3 + _PUNCT2:
            if src.startswith(p, i):
                i += len(p)
                out.append(Tok("punct", p, start_line, start, i))
                break
        else:
            i += 1
            out.append(Tok("punct", c, start_line, start, i))
    return out


def _scan_char(src: str, i: int) -> Optional[int]:
    """If src[i] starts a char literal ('x', '\\n', '\\u{..}'), return end index."""
    n = len(src)
    if i >= n or src[i] != "'":
        return None
    j = i + 1
    if j >= n:
        return None
    if src[j] == "\\":
        j += 1
        if j < n and src[j] == "u" and j + 1 < n and src[j + 1] == "{":
            k = src.find("}", j)
            if k < 0:
                return None
            j = k + 1
        elif j < n and src[j] == "x":
            j += 3
        else:
            j += 1
    else:
        if src[j] in "\n'":
            return None
        j += 1
    if j < n and src[j] == "'":
        return j + 1
    return None


def tokenize(src: str) -> List[Tok]:
    flat = lex(src)
    root: List[Tok] = []
    stack: List[Tuple[Tok, List[Tok]]] = []
    cur = root
    for t in flat:
        if t.kind == "punct" and t.text in _OPEN:
            g = Tok("group", t.text + _OPEN[t.text], t.line, t.start, t.end, delim=t.text)
            stack.append((g, cur))
            cur = g.children
            continue
        if t.kind == "punct" and t.text in _CLOSE:
            if not stack:
                raise RustParseError(f"line {t.line}: unmatched '{t.text}'")
            g, parent = stack.pop()
            if _OPEN[g.delim] != t.text:
                raise RustParseError(f"line {t.line}: mismatched '{t.text}' for '{g.delim}' opened on line {g.line}")
            g.end = t.end
            parent.append(g)
            cur = parent
            continue
        cur.append(t)
    if stack:
        g, _ = stack[-1]
        raise RustParseError(f"line {g.line}: unclosed '{g.delim}'")
    return root


def tokens_text(toks: Sequence[Tok]) -> str:
    parts: List[str] = []
    for t in toks:
        if t.kind == "group":
            parts.append(t.delim + " " + tokens_text(t.children) + " " + _OPEN[t.delim])
        else:
            parts.append(t.text)
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #

@dataclass
class Attr:
    inner: bool
    group: Tok  # the [...] group

    @property
    def toks(self) -> List[Tok]:
        return self.group.children

    def path(self) -> str:
        parts = []
        for t in self.toks:
            if t.kind == "ident":
                parts.append(t.text)
            elif t.is_punct("::"):
                continue
            else:
                break
        return "::".join(parts)

    def args(self) -> Optional[Tok]:
        for t in self.toks:
            if t.is_group("("):
                return t
        return None

    def text(self) -> str:
        return ("#![" if self.inner else "#[") + tokens_text(self.toks) + "]"


@dataclass
class Item:
    kind: str  # fn | mod | impl | trait | struct | enum | union | type | const | static | use | macro | other
    name: str
    attrs: List[Attr]
    toks: List[Tok]  # all tokens of the item (without outer attrs)
    line: int
    start: int
    body: Optional[Tok] = None  # brace group for fn/mod/impl/trait/...
    is_pub: bool = False
    children: List["Item"] = field(default_factory=list)
    inner_attrs: List[Attr] = field(default_factory=list)

    def header_end(self) -> int:
        return self.body.start if self.body is not None else (self.toks[-1].end if self.toks else self.start)


_QUALIFIERS = {"async", "const", "unsafe", "extern", "default", "safe"}


def parse_items(toks: Sequence[Tok]) -> Tuple[List[Attr], List[Item]]:
    inner: List[Attr] = []
    items: List[Item] = []
    pending: List[Attr] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t.is_punct("#"):
            if i + 2 < n and toks[i + 1].is_punct("!") and toks[i + 2].is_group("["):
                inner.append(Attr(True, toks[i + 2]))
                i += 3
                continue
            if i + 1 < n and toks[i + 1].is_group("["):
                pending.append(Attr(False, toks[i + 1]))
                i += 2
                continue
        if t.is_punct(";"):
            i += 1
            pending = []
            continue
        j, item = _parse_one(toks, i)
        item.attrs = pending
        pending = []
        if item.body is not None and item.kind in ("mod", "impl", "trait"):
            sub_inner, sub_items = parse_items(item.body.children)
            item.children = sub_items
            item.inner_attrs = sub_inner
        items.append(item)
        i = j
    return inner, items


def _parse_one(toks: Sequence[Tok], i: int) -> Tuple[int, Item]:
    n = len(toks)
    start = i
    is_pub = False
    k = i
    if toks[k].is_ident("pub"):
        is_pub = True
        k += 1
        if k < n and toks[k].is_group("("):
            k += 1
    while k < n and toks[k].kind == "ident" and toks[k].text in _QUALIFIERS:
        if toks[k].text == "const" and k + 1 < n and toks[k + 1].kind == "ident" and toks[k + 1].text not in ("fn", "unsafe", "async", "extern"):
            break  # `const NAME: T = ...;`
        k += 1
        if k < n and toks[k].kind == "lit":  # extern "C"
            k += 1
    kw = toks[k].text if k < n and toks[k].kind == "ident" else ""

    def to_body_or_semi(j: int) -> Tuple[int, Optional[Tok]]:
        while j < n:
            if toks[j].is_group("{"):
                return j + 1, toks[j]
            if toks[j].is_punct(";"):
                return j + 1, None
            j += 1
        return j, None

    def to_semi(j: int) -> int:
        while j < n and not toks[j].is_punct(";"):
            j += 1
        return min(j + 1, n)

    name = ""
    kind = "other"
    body: Optional[Tok] = None
    if kw in ("fn", "mod", "trait", "struct", "enum", "union", "impl"):
        kind = kw
        if kw != "impl" and k + 1 < n and toks[k + 1].kind == "ident":
            name = toks[k + 1].text
        if kw == "impl":
            # name = type the impl is for (best effort)
            j2, body = to_body_or_semi(k + 1)
            hdr = toks[k + 1: j2 - 1] if body is not None else toks[k + 1: j2]
            name = tokens_text(hdr)
            end = j2
        else:
            end, body = to_body_or_semi(k + 1)
            if kind in ("struct", "union") and body is None:
                pass
            if body is not None and kind == "struct" and end < n and toks[end].is_punct(";"):
                end += 1
    elif kw == "macro_rules" and k + 1 < n and toks[k + 1].is_punct("!"):
        kind = "macro"
        name = toks[k + 2].text if k + 2 < n else ""
        end = k + 3
        if end < n and toks[end].kind == "group":
            end += 1
        if end < n and toks[end].is_punct(";"):
            end += 1
    elif kw and k + 1 < n and toks[k + 1].is_punct("!"):
        kind = "macro_call"
        name = kw
        end = k + 2
        if end < n and toks[end].kind == "group":
            end += 1
        if end < n and toks[end].is_punct(";"):
            end += 1
    elif kw in ("type", "const", "static", "use"):
        kind = kw
        if kw != "use" and k + 1 < n and toks[k + 1].kind == "ident":
            name = toks[k + 1].text if toks[k + 1].text != "mut" else (toks[k + 2].text if k + 2 < n else "")
        end = to_semi(k + 1)
    elif kw == "extern" or (k < n and toks[k].is_ident("extern")):
        kind = "extern"
        end, body = to_body_or_semi(k + 1)
    else:
        end, body = to_body_or_semi(k)
    end = max(end, start + 1)
    item_toks = list(toks[start:end])
    return end, Item(kind, name, [], item_toks, toks[start].line, toks[start].start, body=body, is_pub=is_pub)


# --------------------------------------------------------------------------- #
# cfg evaluation
# --------------------------------------------------------------------------- #

def _split_commas(toks: Sequence[Tok]) -> List[List[Tok]]:
    parts: List[List[Tok]] = [[]]
    for t in toks:
        if t.is_punct(","):
            parts.append([])
        else:
            parts[-1].append(t)
    return [p for p in parts if p]


def eval_cfg(toks: Sequence[Tok]) -> Optional[bool]:
    """Tri-state evaluation of a cfg predicate under `cargo test`.

    True / False when statically known, None when it depends on the build
    (features, target, ...). ``test`` counts as true: we ask "is this code
    compiled when the test suite runs?".
    """
    if not toks:
        return None
    head = toks[0]
    if head.kind != "ident":
        return None
    name = head.text
    args = toks[1] if len(toks) > 1 and toks[1].is_group("(") else None
    if args is None:
        if len(toks) == 1:
            if name in ("test", "true"):
                return True
            if name in ("FALSE", "false", "never", "disabled"):
                # Not real cfg names; never set by cargo. Treat as false.
                return False
            return None
        return None  # key = "value"
    subs = [eval_cfg(p) for p in _split_commas(args.children)]
    if name == "any":
        if any(s is True for s in subs):
            return True
        if all(s is False for s in subs):
            return False  # includes any() with no arguments
        return None
    if name == "all":
        if any(s is False for s in subs):
            return False
        if all(s is True for s in subs):
            return True  # includes all()
        return None
    if name == "not":
        if len(subs) != 1 or subs[0] is None:
            return None
        return not subs[0]
    return None


def cfg_mentions_test(toks: Sequence[Tok]) -> bool:
    for t in toks:
        if t.is_ident("test"):
            return True
        if t.kind == "group" and cfg_mentions_test(t.children):
            return True
    return False


# --------------------------------------------------------------------------- #
# Anti-cheat analysis
# --------------------------------------------------------------------------- #

TEST_ATTR_NAMES = {"test", "rstest", "test_case", "quickcheck", "wasm_bindgen_test", "googletest"}


@dataclass
class TestFn:
    qualname: str
    line: int
    fingerprint: str


@dataclass
class Finding:
    kind: str
    qualname: str
    line: int
    fingerprint: str
    message: str


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _walk_calls(toks: Sequence[Tok]) -> Iterator[Tuple[str, str]]:
    """Yield ("macro"|"call", name) for ``name!`` and ``name(`` patterns, recursively."""
    for idx, t in enumerate(toks):
        if t.kind == "group":
            yield from _walk_calls(t.children)
            continue
        if t.kind != "ident":
            continue
        nxt = toks[idx + 1] if idx + 1 < len(toks) else None
        if nxt is None:
            continue
        if nxt.is_punct("!") and idx + 2 < len(toks) and toks[idx + 2].kind == "group":
            yield ("macro", t.text)
        elif nxt.is_group("("):
            yield ("call", t.text)


def is_assertion(kind: str, name: str, extra: Sequence[str]) -> bool:
    low = name.lower()
    if name in extra:
        return True
    if kind == "macro":
        return "assert" in low or name in ("panic",)
    return low.startswith("assert")


def _attr_disables(attr: Attr) -> Optional[str]:
    """Return a reason if the attribute statically removes the item from test builds."""
    p = attr.path()
    if p == "cfg":
        args = attr.args()
        if args is not None and eval_cfg(args.children) is False:
            return f"#[cfg({tokens_text(args.children)})] is always false under `cargo test`"
    return None


def _attr_ignores(attr: Attr) -> bool:
    p = attr.path()
    if p == "ignore":
        return True
    if p == "cfg_attr":
        args = attr.args()
        if args is None:
            return False
        parts = _split_commas(args.children)
        if not parts:
            return False
        if eval_cfg(parts[0]) is False:
            return False
        for sub in parts[1:]:
            if sub and sub[0].is_ident("ignore"):
                return True
    return False


def _is_test_attr(attr: Attr) -> bool:
    path = attr.path()
    if not path:
        return False
    last = path.split("::")[-1]
    return last in TEST_ATTR_NAMES


_LINT_ALLOW_PATHS = ("allow", "expect")
_BLANKET_LINTS = {"warnings", "clippy::all", "clippy::correctness", "clippy::suspicious", "clippy::complexity",
                  "clippy::perf", "clippy::style", "clippy::pedantic", "unused", "dead_code"}


def _lint_allow_text(attr: Attr) -> Optional[str]:
    p = attr.path()
    if p in _LINT_ALLOW_PATHS:
        return attr.text()
    if p == "cfg_attr":
        args = attr.args()
        if args is not None:
            for sub in _split_commas(args.children)[1:]:
                if sub and sub[0].kind == "ident" and sub[0].text in _LINT_ALLOW_PATHS:
                    return attr.text()
    return None


def _blanket_lints(attr: Attr) -> List[str]:
    if attr.path() not in _LINT_ALLOW_PATHS:
        return []
    args = attr.args()
    if args is None:
        return []
    names = []
    for part in _split_commas(args.children):
        names.append("".join(t.text for t in part if t.kind != "group"))
    return [n for n in names if n in _BLANKET_LINTS]


def analyze_source(src: str, is_test_file: bool, extra_assert_names: Sequence[str] = ()) -> Tuple[List[TestFn], List[Finding]]:
    """Analyze one Rust file. Raises RustParseError on malformed input."""
    tree = tokenize(src)
    inner, items = parse_items(tree)
    tests: List[TestFn] = []
    findings: List[Finding] = []

    for a in inner:
        reason = _attr_disables(a)
        if reason and is_test_file:
            findings.append(Finding("cfg_disabled", "<file>", a.group.line, _fingerprint(a.text()),
                                    f"whole test file disabled: {reason}"))
        lt = _lint_allow_text(a)
        if lt:
            findings.append(Finding("lint_allow", "<file>", a.group.line, _fingerprint(lt),
                                    f"crate/module-level lint suppression `{lt}`"))

    def visit(items: List[Item], path: List[str], test_ctx: bool) -> None:
        for it in items:
            qual = "::".join(path + [it.name or it.kind])
            disabled = None
            for a in it.attrs:
                disabled = disabled or _attr_disables(a)
                for lint in _blanket_lints(a):
                    findings.append(Finding("lint_allow", qual, a.group.line, _fingerprint(qual, a.text()),
                                            f"blanket lint suppression `{a.text()}` on `{qual}`"))
            is_test = it.kind == "fn" and any(_is_test_attr(a) for a in it.attrs)
            fp = _fingerprint(tokens_text([x.group for x in it.attrs]), tokens_text(it.toks))
            if is_test:
                tests.append(TestFn(qual, it.line, fp))
                if disabled:
                    findings.append(Finding("cfg_disabled", qual, it.line, fp, f"test `{qual}` disabled: {disabled}"))
                if any(_attr_ignores(a) for a in it.attrs):
                    findings.append(Finding("ignored", qual, it.line, fp, f"test `{qual}` is marked #[ignore]"))
                body = it.body.children if it.body is not None else []
                if not body:
                    findings.append(Finding("empty", qual, it.line, fp, f"test `{qual}` has an empty body"))
                else:
                    calls = list(_walk_calls(body))
                    should_panic = any(a.path() == "should_panic" for a in it.attrs)
                    if not should_panic and not any(is_assertion(k, nm, extra_assert_names) for k, nm in calls):
                        findings.append(Finding("no_assertion", qual, it.line, fp,
                                                f"test `{qual}` contains no assertion (assert!/assert_eq!/... or #[should_panic])"))
                _todo_findings(body, qual, fp, findings)
                continue
            if it.kind == "mod":
                if disabled and (test_ctx or is_test_file or _module_has_tests(it)):
                    findings.append(Finding("cfg_disabled", qual, it.line, fp, f"test module `{qual}` disabled: {disabled}"))
                mod_test = test_ctx or any(a.path() == "cfg" and a.args() is not None and cfg_mentions_test(a.args().children)
                                           for a in it.attrs)
                for a in it.inner_attrs:
                    lt = _lint_allow_text(a)
                    if lt:
                        findings.append(Finding("lint_allow", qual, a.group.line, _fingerprint(qual, lt),
                                                f"module-level lint suppression `{lt}` in `{qual}`"))
                visit(it.children, path + [it.name], mod_test)
                continue
            if it.kind == "fn" and (test_ctx or is_test_file) and it.body is not None:
                _todo_findings(it.body.children, qual, fp, findings)
            if it.kind in ("impl", "trait") and (test_ctx or is_test_file):
                for ch in it.children:
                    if ch.kind == "fn" and ch.body is not None:
                        cq = qual + "::" + ch.name
                        _todo_findings(ch.body.children, cq, _fingerprint(tokens_text(ch.toks)), findings)

    visit(items, [], False)
    return tests, findings


def _module_has_tests(mod: Item) -> bool:
    for ch in mod.children:
        if ch.kind == "fn" and any(_is_test_attr(a) for a in ch.attrs):
            return True
        if ch.kind == "mod" and _module_has_tests(ch):
            return True
    return False


def _todo_findings(body: Sequence[Tok], qual: str, fp: str, findings: List[Finding]) -> None:
    seen = set()
    for kind, name in _walk_calls(body):
        if kind == "macro" and name in ("todo", "unimplemented") and name not in seen:
            seen.add(name)
            findings.append(Finding(name, qual, 0, fp, f"`{name}!()` inside test code `{qual}`"))


# --------------------------------------------------------------------------- #
# Public signatures for pipeline.py context
# --------------------------------------------------------------------------- #

def _collapse(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_signatures(src: str) -> List[str]:
    """``pub fn``/``pub struct``/``pub enum``/``pub trait``/``pub type``/``pub const`` + ``impl`` blocks."""
    try:
        tree = tokenize(src)
        _, items = parse_items(tree)
    except RustParseError:
        return []
    out: List[str] = []

    def header(it: Item) -> str:
        first = it.toks[0].start if it.toks else it.start
        return _collapse(src[first:it.header_end()]).rstrip("{; ").strip()

    def walk(items: List[Item], prefix: str) -> None:
        for it in items:
            if it.kind == "mod" and it.body is not None:
                if any(a.path() == "cfg" and a.args() is not None and cfg_mentions_test(a.args().children) for a in it.attrs):
                    continue
                walk(it.children, prefix + it.name + "::")
                continue
            if it.kind == "impl":
                methods = []
                trait_impl = " for " in f" {it.name} "
                for ch in it.children:
                    if ch.kind == "fn" and (ch.is_pub or trait_impl):
                        methods.append("    " + header(ch) + ";")
                    elif ch.kind in ("type", "const") and (ch.is_pub or trait_impl):
                        methods.append("    " + _collapse(src[ch.toks[0].start:ch.toks[-1].end]))
                sig = (prefix and f"// in {prefix.rstrip(':')}\n" or "") + header(it) + " {"
                out.append(sig + ("\n" + "\n".join(methods) if methods else "") + "\n}")
                continue
            if not it.is_pub:
                continue
            if it.kind == "fn":
                out.append(header(it) + ";")
            elif it.kind in ("struct", "enum", "union"):
                text = header(it)
                if it.body is not None:
                    if it.kind == "enum":
                        variants = [p[0].text for p in _split_commas(it.body.children)
                                    if p and p[0].kind == "ident"]
                        text += " { " + ", ".join(variants) + " }"
                    else:
                        fields = []
                        for p in _split_commas(it.body.children):
                            p2 = [t for t in p if not (t.is_punct("#") or t.is_group("["))]
                            if p2 and p2[0].is_ident("pub"):
                                fields.append(_collapse(src[p2[0].start:p2[-1].end]))
                        text += " { " + ", ".join(fields) + (" .. }" if fields else ".. }")
                else:
                    text = _collapse(src[it.toks[0].start:it.toks[-1].end]).rstrip(";") + ";"
                out.append(text)
            elif it.kind == "trait":
                methods = ["    " + header(ch) + ";" for ch in it.children if ch.kind == "fn"]
                out.append(header(it) + " {" + ("\n" + "\n".join(methods) if methods else "") + "\n}")
            elif it.kind in ("type", "const", "static"):
                text = _collapse(src[it.toks[0].start:it.toks[-1].end])
                if it.kind != "type" and "=" in text:
                    text = text.split("=", 1)[0].strip() + ";"
                out.append(text)

    walk(items, "")
    return out


def extract_module_refs(src: str) -> Tuple[List[str], List[str]]:
    """Return (``mod foo;`` declarations, ``use crate::a::b`` paths as 'a::b')."""
    try:
        _, items = parse_items(tokenize(src))
    except RustParseError:
        return [], []
    mods, uses = [], []
    for it in items:
        if it.kind == "mod" and it.body is None and it.name:
            mods.append(it.name)
        if it.kind == "use":
            toks = [t for t in it.toks if t.kind in ("ident",) or t.is_punct("::")]
            idents = [t.text for t in toks if t.kind == "ident" and t.text not in ("pub", "use")]
            if idents and idents[0] == "crate" and len(idents) > 1:
                uses.append("::".join(idents[1:]))
    return mods, uses
