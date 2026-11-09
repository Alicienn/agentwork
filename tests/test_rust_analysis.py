"""Rust analyzers: the pure-Python builtin and the syn helper must agree."""

from __future__ import annotations

import pytest

from awlib import rust_checks, rustsrc
from conftest import TEMPLATE, requires_cargo

TRICKY = r'''
#![allow(clippy::all)]
//! doc with a } brace and a " quote
use crate::parser::Node;
/* nested /* block */ comment with { */
pub fn add<'a>(a: &'a str, b: char) -> usize where 'a: 'a {
    let _ = r#"raw " { string "#; let _c = '{'; let _b = b'}'; let _l: &'static str = "x}"; a.len()
}
pub struct Point { pub x: i32, y: i32 }
pub enum E { A, B(u8), C { x: u8 } }
pub trait T { fn go(&self) -> u8; }
impl T for Point { fn go(&self) -> u8 { 1 } }
impl Point { pub fn new() -> Self { Point { x: 0, y: 0 } } fn private() {} }

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn ok() { assert_eq!(add("a", 'b'), 1); }
    #[test] #[ignore] fn ign() { assert!(true); }
    #[test] #[ignore = "slow"] fn ign2() { assert!(true); }
    #[test] fn empty() {}
    #[test] fn noassert() { let _ = add("x", 'y'); }
    #[test] fn td() { assert!(todo!()); }
    #[test] fn unimpl() { unimplemented!() }
    #[cfg(any())] #[test] fn dis() { assert!(false); }
    #[cfg(not(test))] #[test] fn dis2() { assert!(false); }
    #[cfg(all(unix, any()))] #[test] fn dis3() { assert!(false); }
    #[cfg(feature = "slow")] #[test] fn maybe() { assert!(true); }
    #[tokio::test] async fn asy() { foo().await.assert_ok(); }
    #[cfg(any())] mod off { #[test] fn x() { assert!(true); } }
    #[test] #[should_panic] fn sp() { add("", 'a'); }
    #[test] #[cfg_attr(not(windows), ignore)] fn ci() { assert!(true); }
    #[test] #[cfg_attr(any(), ignore)] fn never_ignored() { assert!(true); }
    #[test] fn in_macro() { vec![assert_eq!(1, 1)]; }
    #[test] fn via_helper() { check_it(1); }
    #[allow(warnings)] fn helper() {}
}
'''

EXPECTED = {
    ("lint_allow", "<file>"),
    ("ignored", "tests::ign"),
    ("ignored", "tests::ign2"),
    ("empty", "tests::empty"),
    ("no_assertion", "tests::noassert"),
    ("todo", "tests::td"),
    ("unimplemented", "tests::unimpl"),
    ("no_assertion", "tests::unimpl"),
    ("cfg_disabled", "tests::dis"),
    ("cfg_disabled", "tests::dis2"),
    ("cfg_disabled", "tests::dis3"),
    ("cfg_disabled", "tests::off"),
    ("ignored", "tests::ci"),
    ("no_assertion", "tests::via_helper"),
    ("lint_allow", "tests::helper"),
}


def _kinds(analysis):
    return {(f[0], f[1]) for f in analysis.findings}


def test_builtin_analyzer_findings():
    a = rust_checks.analyze_with_builtin(TRICKY, False, [])
    assert a.parse_error is None
    assert _kinds(a) == EXPECTED
    names = {t[0] for t in a.tests}
    assert {"tests::ok", "tests::asy", "tests::maybe", "tests::sp", "tests::never_ignored", "tests::in_macro"} <= names


def test_extra_assertion_names():
    a = rust_checks.analyze_with_builtin(TRICKY, False, ["check_it"])
    assert ("no_assertion", "tests::via_helper") not in _kinds(a)


def test_todo_anywhere_in_tests_dir_file():
    src = "fn helper() -> u8 { todo!() }\n#[test] fn t() { assert_eq!(helper(), 1); }\n"
    assert ("todo", "helper") in _kinds(rust_checks.analyze_with_builtin(src, True, []))
    # in src/, a todo!() outside test code is the implementation's business
    assert ("todo", "helper") not in _kinds(rust_checks.analyze_with_builtin(src, False, []))


def test_whole_test_file_disabled():
    src = "#![cfg(any())]\n#[test] fn t() { assert!(true); }\n"
    assert ("cfg_disabled", "<file>") in _kinds(rust_checks.analyze_with_builtin(src, True, []))


def test_parse_errors_are_reported():
    a = rust_checks.analyze_with_builtin("fn x() { let s = \"unterminated; }", False, [])
    assert a.parse_error
    a = rust_checks.analyze_with_builtin("fn x() { ( }", False, [])
    assert a.parse_error


def test_lexer_edge_cases():
    toks = rustsrc.lex("let x = 'a'; let y: &'static str = r##\"a \"# b\"##; br\"x\"; c\"y\"; 1.0e-3; 0..10; x.0;")
    kinds = [(t.kind, t.text) for t in toks]
    assert ("lit", "'a'") in kinds
    assert ("lifetime", "'static") in kinds
    assert ("lit", 'r##"a "# b"##') in kinds
    assert ("lit", 'br"x"') in kinds and ("lit", 'c"y"') in kinds
    assert ("lit", "1.0e-3") in kinds
    assert ("punct", "..") in kinds


def test_signatures():
    sigs = rustsrc.extract_signatures(TRICKY)
    joined = "\n".join(sigs)
    assert "pub fn add<'a>(a: &'a str, b: char) -> usize where 'a: 'a;" in sigs
    assert "pub struct Point { pub x: i32 .. }" in sigs
    assert "pub enum E { A, B, C }" in sigs
    assert "pub trait T {\n    fn go(&self) -> u8;\n}" in sigs
    assert "impl T for Point {\n    fn go(&self) -> u8;\n}" in sigs
    assert "impl Point {\n    pub fn new() -> Self;\n}" in sigs
    assert "private" not in joined
    assert "fn ok" not in joined  # test modules are skipped
    assert rustsrc.extract_module_refs("mod a;\nmod b { }\nuse crate::x::y;\nuse std::io;\n") == (["a"], ["x::y"])


@requires_cargo
def test_syn_helper_matches_builtin():
    exe = rust_checks.build_syn_checker(TEMPLATE)
    a_syn = rust_checks.analyze_with_syn(exe, TRICKY, False, [])
    a_py = rust_checks.analyze_with_builtin(TRICKY, False, [])
    assert a_syn.parse_error is None
    assert _kinds(a_syn) == _kinds(a_py) == EXPECTED
    assert {t[0] for t in a_syn.tests} == {t[0] for t in a_py.tests}
    for extra_src, is_test in (("#![cfg(any())]\n#[test] fn t() { assert!(true); }\n", True),
                               ("fn helper() -> u8 { todo!() }\n#[test] fn t() { assert_eq!(helper(), 1); }\n", True)):
        assert _kinds(rust_checks.analyze_with_syn(exe, extra_src, is_test, [])) == \
            _kinds(rust_checks.analyze_with_builtin(extra_src, is_test, []))
    bad = rust_checks.analyze_with_syn(exe, "fn x() { ( }", False, [])
    assert bad.parse_error


@pytest.mark.parametrize("checker", ["builtin", pytest.param("syn", marks=requires_cargo)])
def test_rust_check_cli(tmp_path, checker, capsys):
    f = tmp_path / "t.rs"
    f.write_text("#[test] #[ignore] fn t() { assert!(true); }\n", encoding="utf-8")
    rc = rust_checks.cli_main(["--checker", checker, str(f)])
    out = capsys.readouterr().out
    assert rc == 1 and "[ignored]" in out
