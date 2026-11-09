//! agentwork-rust-testcheck — parse one Rust file with `syn` and report test anti-patterns.
//!
//! Usage: agentwork-rust-testcheck [--test-file] [--assert-name NAME]... <file.rs>
//!
//! Output, one record per line, tab separated:
//!   TEST  <qualname> <line> <fingerprint>
//!   FIND  <kind> <qualname> <line> <fingerprint> <message>
//!   PARSE_ERROR <line> <message>
//!
//! Finding kinds (shared with the Python fallback in awlib/rustsrc.py):
//!   ignored, empty, no_assertion, todo, unimplemented, cfg_disabled, lint_allow
//!
//! Exit code is 0 whenever the file could be read (findings are data);
//! 2 on usage / IO errors.

use proc_macro2::{Delimiter, TokenStream, TokenTree};
use quote::ToTokens;
use std::fmt::Write as _;
use syn::{Attribute, ImplItem, Item, TraitItem};

const TEST_ATTR_NAMES: &[&str] = &["test", "rstest", "test_case", "quickcheck", "wasm_bindgen_test", "googletest"];
const BLANKET_LINTS: &[&str] = &[
    "warnings", "clippy::all", "clippy::correctness", "clippy::suspicious", "clippy::complexity",
    "clippy::perf", "clippy::style", "clippy::pedantic", "unused", "dead_code",
];

struct Ctx {
    is_test_file: bool,
    extra: Vec<String>,
    out: String,
}

fn fnv(parts: &[&str]) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for (i, p) in parts.iter().enumerate() {
        if i > 0 {
            h ^= 0x1f;
            h = h.wrapping_mul(0x100000001b3);
        }
        for b in p.as_bytes() {
            h ^= *b as u64;
            h = h.wrapping_mul(0x100000001b3);
        }
    }
    format!("{:016x}", h)
}

fn clean(s: &str) -> String {
    s.replace(['\t', '\n', '\r'], " ")
}

impl Ctx {
    fn test(&mut self, qual: &str, line: usize, fp: &str) {
        let _ = writeln!(self.out, "TEST\t{}\t{}\t{}", qual, line, fp);
    }
    fn find(&mut self, kind: &str, qual: &str, line: usize, fp: &str, msg: &str) {
        let _ = writeln!(self.out, "FIND\t{}\t{}\t{}\t{}\t{}", kind, qual, line, fp, clean(msg));
    }
}

fn path_string(attr: &Attribute) -> String {
    attr.path()
        .segments
        .iter()
        .map(|s| s.ident.to_string())
        .collect::<Vec<_>>()
        .join("::")
}

fn attr_args(attr: &Attribute) -> Option<TokenStream> {
    match &attr.meta {
        syn::Meta::List(l) => Some(l.tokens.clone()),
        _ => None,
    }
}

fn attr_text(attr: &Attribute) -> String {
    attr.to_token_stream().to_string()
}

fn split_commas(ts: TokenStream) -> Vec<Vec<TokenTree>> {
    let mut parts: Vec<Vec<TokenTree>> = vec![Vec::new()];
    for tt in ts {
        match &tt {
            TokenTree::Punct(p) if p.as_char() == ',' => parts.push(Vec::new()),
            _ => parts.last_mut().unwrap().push(tt),
        }
    }
    parts.into_iter().filter(|p| !p.is_empty()).collect()
}

/// Tri-state cfg evaluation under `cargo test` (`test` is true).
fn eval_cfg(toks: &[TokenTree]) -> Option<bool> {
    let name = match toks.first()? {
        TokenTree::Ident(i) => i.to_string(),
        _ => return None,
    };
    if toks.len() == 1 {
        return match name.as_str() {
            "test" | "true" => Some(true),
            "false" | "FALSE" | "never" | "disabled" => Some(false),
            _ => None,
        };
    }
    let group = match &toks[1] {
        TokenTree::Group(g) if g.delimiter() == Delimiter::Parenthesis => g,
        _ => return None,
    };
    let subs: Vec<Option<bool>> = split_commas(group.stream()).iter().map(|p| eval_cfg(p)).collect();
    match name.as_str() {
        "any" => {
            if subs.iter().any(|s| *s == Some(true)) {
                Some(true)
            } else if subs.iter().all(|s| *s == Some(false)) {
                Some(false)
            } else {
                None
            }
        }
        "all" => {
            if subs.iter().any(|s| *s == Some(false)) {
                Some(false)
            } else if subs.iter().all(|s| *s == Some(true)) {
                Some(true)
            } else {
                None
            }
        }
        "not" => {
            if subs.len() == 1 {
                subs[0].map(|b| !b)
            } else {
                None
            }
        }
        _ => None,
    }
}

fn mentions_test(ts: TokenStream) -> bool {
    ts.into_iter().any(|tt| match tt {
        TokenTree::Ident(i) => i == "test",
        TokenTree::Group(g) => mentions_test(g.stream()),
        _ => false,
    })
}

fn attr_disables(attr: &Attribute) -> Option<String> {
    if path_string(attr) != "cfg" {
        return None;
    }
    let args = attr_args(attr)?;
    let toks: Vec<TokenTree> = args.clone().into_iter().collect();
    if eval_cfg(&toks) == Some(false) {
        Some(format!("#[cfg({})] is always false under `cargo test`", args))
    } else {
        None
    }
}

fn attr_ignores(attr: &Attribute) -> bool {
    let p = path_string(attr);
    if p == "ignore" {
        return true;
    }
    if p == "cfg_attr" {
        if let Some(args) = attr_args(attr) {
            let parts = split_commas(args);
            if parts.is_empty() || eval_cfg(&parts[0]) == Some(false) {
                return false;
            }
            return parts[1..].iter().any(|sub| matches!(sub.first(), Some(TokenTree::Ident(i)) if i == "ignore"));
        }
    }
    false
}

fn is_test_attr(attr: &Attribute) -> bool {
    attr.path()
        .segments
        .last()
        .map(|s| TEST_ATTR_NAMES.contains(&s.ident.to_string().as_str()))
        .unwrap_or(false)
}

fn lint_allow_text(attr: &Attribute) -> Option<String> {
    let p = path_string(attr);
    if p == "allow" || p == "expect" {
        return Some(attr_text(attr));
    }
    if p == "cfg_attr" {
        if let Some(args) = attr_args(attr) {
            let parts = split_commas(args);
            if parts.len() > 1
                && parts[1..].iter().any(|sub| matches!(sub.first(), Some(TokenTree::Ident(i)) if i == "allow" || i == "expect"))
            {
                return Some(attr_text(attr));
            }
        }
    }
    None
}

fn blanket_lints(attr: &Attribute) -> Vec<String> {
    let p = path_string(attr);
    if p != "allow" && p != "expect" {
        return vec![];
    }
    let args = match attr_args(attr) {
        Some(a) => a,
        None => return vec![],
    };
    split_commas(args)
        .iter()
        .map(|part| part.iter().map(|t| t.to_string()).collect::<String>())
        .filter(|n| BLANKET_LINTS.contains(&n.as_str()))
        .collect()
}

/// Yield ("macro"|"call", name) for `name!(..)` and `name(..)`, recursively (macro bodies included).
fn walk_calls(ts: TokenStream, out: &mut Vec<(&'static str, String)>) {
    let toks: Vec<TokenTree> = ts.into_iter().collect();
    for (idx, t) in toks.iter().enumerate() {
        match t {
            TokenTree::Group(g) => walk_calls(g.stream(), out),
            TokenTree::Ident(i) => {
                let next = toks.get(idx + 1);
                match next {
                    Some(TokenTree::Punct(p)) if p.as_char() == '!' => {
                        if matches!(toks.get(idx + 2), Some(TokenTree::Group(_))) {
                            out.push(("macro", i.to_string()));
                        }
                    }
                    Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis => {
                        out.push(("call", i.to_string()));
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }
}

fn is_assertion(kind: &str, name: &str, extra: &[String]) -> bool {
    if extra.iter().any(|e| e == name) {
        return true;
    }
    let low = name.to_lowercase();
    if kind == "macro" {
        low.contains("assert") || name == "panic"
    } else {
        low.starts_with("assert")
    }
}

fn todo_findings(ctx: &mut Ctx, body: TokenStream, qual: &str, line: usize, fp: &str) {
    let mut calls = Vec::new();
    walk_calls(body, &mut calls);
    let mut seen: Vec<String> = Vec::new();
    for (kind, name) in calls {
        if kind == "macro" && (name == "todo" || name == "unimplemented") && !seen.contains(&name) {
            seen.push(name.clone());
            ctx.find(&name, qual, line, fp, &format!("`{}!()` inside test code `{}`", name, qual));
        }
    }
}

fn module_has_tests(items: &[Item]) -> bool {
    items.iter().any(|it| match it {
        Item::Fn(f) => f.attrs.iter().any(is_test_attr),
        Item::Mod(m) => m.content.as_ref().map(|(_, i)| module_has_tests(i)).unwrap_or(false),
        _ => false,
    })
}

fn item_attrs(it: &Item) -> &[Attribute] {
    match it {
        Item::Fn(x) => &x.attrs,
        Item::Mod(x) => &x.attrs,
        Item::Impl(x) => &x.attrs,
        Item::Trait(x) => &x.attrs,
        Item::Struct(x) => &x.attrs,
        Item::Enum(x) => &x.attrs,
        Item::Const(x) => &x.attrs,
        Item::Static(x) => &x.attrs,
        Item::Use(x) => &x.attrs,
        Item::Type(x) => &x.attrs,
        Item::Union(x) => &x.attrs,
        Item::Macro(x) => &x.attrs,
        _ => &[],
    }
}

fn line_of<T: ToTokens>(node: &T) -> usize {
    node.to_token_stream()
        .into_iter()
        .next()
        .map(|t| t.span().start().line)
        .unwrap_or(0)
}

fn visit(ctx: &mut Ctx, items: &[Item], path: &[String], test_ctx: bool) {
    for it in items {
        let attrs = item_attrs(it);
        let outer: Vec<&Attribute> = attrs.iter().filter(|a| matches!(a.style, syn::AttrStyle::Outer)).collect();
        let name = match it {
            Item::Fn(f) => f.sig.ident.to_string(),
            Item::Mod(m) => m.ident.to_string(),
            Item::Impl(i) => format!("impl {}", i.self_ty.to_token_stream()),
            Item::Trait(t) => t.ident.to_string(),
            _ => "item".to_string(),
        };
        let mut qv = path.to_vec();
        qv.push(name.clone());
        let qual = qv.join("::");
        let disabled = outer.iter().find_map(|a| attr_disables(a));
        for a in &outer {
            for _lint in blanket_lints(a) {
                let t = attr_text(a);
                ctx.find("lint_allow", &qual, line_of(*a), &fnv(&[&qual, &t]),
                         &format!("blanket lint suppression `{}` on `{}`", t, qual));
            }
        }
        let line = line_of(it);
        let fp = fnv(&[&it.to_token_stream().to_string()]);
        match it {
            Item::Fn(f) if outer.iter().any(|a| is_test_attr(a)) => {
                ctx.test(&qual, line, &fp);
                if let Some(r) = &disabled {
                    ctx.find("cfg_disabled", &qual, line, &fp, &format!("test `{}` disabled: {}", qual, r));
                }
                if outer.iter().any(|a| attr_ignores(a)) {
                    ctx.find("ignored", &qual, line, &fp, &format!("test `{}` is marked #[ignore]", qual));
                }
                if f.block.stmts.is_empty() {
                    ctx.find("empty", &qual, line, &fp, &format!("test `{}` has an empty body", qual));
                } else {
                    let should_panic = outer.iter().any(|a| path_string(a) == "should_panic");
                    let mut calls = Vec::new();
                    walk_calls(f.block.to_token_stream(), &mut calls);
                    if !should_panic && !calls.iter().any(|(k, n)| is_assertion(k, n, &ctx.extra)) {
                        ctx.find("no_assertion", &qual, line, &fp,
                                 &format!("test `{}` contains no assertion (assert!/assert_eq!/... or #[should_panic])", qual));
                    }
                }
                todo_findings(ctx, f.block.to_token_stream(), &qual, line, &fp);
            }
            Item::Fn(f) => {
                if test_ctx || ctx.is_test_file {
                    todo_findings(ctx, f.block.to_token_stream(), &qual, line, &fp);
                }
            }
            Item::Mod(m) => {
                let sub: &[Item] = m.content.as_ref().map(|(_, i)| i.as_slice()).unwrap_or(&[]);
                if let Some(r) = &disabled {
                    if test_ctx || ctx.is_test_file || module_has_tests(sub) {
                        ctx.find("cfg_disabled", &qual, line, &fp, &format!("test module `{}` disabled: {}", qual, r));
                    }
                }
                let mod_test = test_ctx
                    || outer.iter().any(|a| path_string(a) == "cfg" && attr_args(a).map(mentions_test).unwrap_or(false));
                for a in attrs.iter().filter(|a| matches!(a.style, syn::AttrStyle::Inner(_))) {
                    if let Some(t) = lint_allow_text(a) {
                        ctx.find("lint_allow", &qual, line_of(a), &fnv(&[&qual, &t]),
                                 &format!("module-level lint suppression `{}` in `{}`", t, qual));
                    }
                }
                visit(ctx, sub, &qv, mod_test);
            }
            Item::Impl(imp) if test_ctx || ctx.is_test_file => {
                for ii in &imp.items {
                    if let ImplItem::Fn(f) = ii {
                        let cq = format!("{}::{}", qual, f.sig.ident);
                        let cfp = fnv(&[&f.to_token_stream().to_string()]);
                        todo_findings(ctx, f.block.to_token_stream(), &cq, line_of(f), &cfp);
                    }
                }
            }
            Item::Trait(tr) if test_ctx || ctx.is_test_file => {
                for ti in &tr.items {
                    if let TraitItem::Fn(f) = ti {
                        if let Some(b) = &f.default {
                            let cq = format!("{}::{}", qual, f.sig.ident);
                            let cfp = fnv(&[&f.to_token_stream().to_string()]);
                            todo_findings(ctx, b.to_token_stream(), &cq, line_of(f), &cfp);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

fn main() {
    let mut is_test_file = false;
    let mut extra: Vec<String> = Vec::new();
    let mut path: Option<String> = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--test-file" => is_test_file = true,
            "--assert-name" => match args.next() {
                Some(n) => extra.push(n),
                None => {
                    eprintln!("--assert-name needs a value");
                    std::process::exit(2);
                }
            },
            "--version" => {
                println!("agentwork-rust-testcheck {}", env!("CARGO_PKG_VERSION"));
                return;
            }
            _ => path = Some(a),
        }
    }
    let path = match path {
        Some(p) => p,
        None => {
            eprintln!("usage: agentwork-rust-testcheck [--test-file] [--assert-name NAME]... <file.rs>");
            std::process::exit(2);
        }
    };
    let src = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("cannot read {}: {}", path, e);
            std::process::exit(2);
        }
    };
    let src = src.strip_prefix('\u{feff}').unwrap_or(&src);
    let file = match syn::parse_file(src) {
        Ok(f) => f,
        Err(e) => {
            println!("PARSE_ERROR\t{}\t{}", e.span().start().line, clean(&e.to_string()));
            return;
        }
    };
    let mut ctx = Ctx { is_test_file, extra, out: String::new() };
    for a in &file.attrs {
        if let Some(r) = attr_disables(a) {
            if is_test_file {
                let t = attr_text(a);
                ctx.find("cfg_disabled", "<file>", line_of(a), &fnv(&[&t]), &format!("whole test file disabled: {}", r));
            }
        }
        if let Some(t) = lint_allow_text(a) {
            ctx.find("lint_allow", "<file>", line_of(a), &fnv(&[&t]), &format!("crate/module-level lint suppression `{}`", t));
        }
    }
    visit(&mut ctx, &file.items, &[], false);
    print!("{}", ctx.out);
}
