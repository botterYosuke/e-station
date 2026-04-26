//! Structural guard for invariant **T35-H8-NoBlockOnInUpdate**.
//!
//! `Flowsurface::update()` must not call `block_on(...)`. All async work
//! goes through `Task::perform(...)` so the iced runtime stays
//! single-threaded and pure.
//!
//! See `docs/plan/tachibana/implementation-plan-T3.5.md` §3 Step A.

use syn::visit::Visit;

struct BlockOnVisitor {
    in_update: bool,
    hits: usize,
}

impl<'ast> Visit<'ast> for BlockOnVisitor {
    fn visit_impl_item_fn(&mut self, i: &'ast syn::ImplItemFn) {
        let was = self.in_update;
        if i.sig.ident == "update" {
            self.in_update = true;
        }
        syn::visit::visit_impl_item_fn(self, i);
        self.in_update = was;
    }

    fn visit_expr_method_call(&mut self, m: &'ast syn::ExprMethodCall) {
        if self.in_update && m.method == "block_on" {
            self.hits += 1;
        }
        syn::visit::visit_expr_method_call(self, m);
    }

    fn visit_expr_call(&mut self, c: &'ast syn::ExprCall) {
        if self.in_update
            && let syn::Expr::Path(p) = c.func.as_ref()
            && p.path
                .segments
                .last()
                .is_some_and(|s| s.ident == "block_on")
        {
            self.hits += 1;
        }
        syn::visit::visit_expr_call(self, c);
    }
}

#[test]
fn update_body_has_no_block_on() {
    let src = std::fs::read_to_string("src/main.rs").expect("read src/main.rs");
    let file = syn::parse_file(&src).expect("parse src/main.rs");
    let mut v = BlockOnVisitor {
        in_update: false,
        hits: 0,
    };
    v.visit_file(&file);
    assert_eq!(
        v.hits, 0,
        "Flowsurface::update() must not call block_on(...); use Task::perform instead"
    );
}
