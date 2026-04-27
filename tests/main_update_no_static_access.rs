//! Structural guard for invariant **T35-H7-NoStaticInUpdate**.
//!
//! `Flowsurface::update()` must not read the `ENGINE_CONNECTION`,
//! `ENGINE_MANAGER`, or `ENGINE_RESTARTING` statics directly. The live
//! connection and manager handles flow into `update()` through the
//! `Flowsurface` struct (populated by a `Subscription`).
//!
//! See `docs/plan/✅tachibana/implementation-plan-T3.5.md` §3 Step A.

use syn::visit::Visit;

const FORBIDDEN_IDENTS: &[&str] = &["ENGINE_CONNECTION", "ENGINE_MANAGER", "ENGINE_RESTARTING"];

struct UpdateBodyVisitor {
    in_update: bool,
    hits: Vec<String>,
}

impl<'ast> Visit<'ast> for UpdateBodyVisitor {
    fn visit_impl_item_fn(&mut self, i: &'ast syn::ImplItemFn) {
        let was = self.in_update;
        if i.sig.ident == "update" {
            self.in_update = true;
        }
        syn::visit::visit_impl_item_fn(self, i);
        self.in_update = was;
    }

    fn visit_path(&mut self, p: &'ast syn::Path) {
        if self.in_update {
            for seg in &p.segments {
                let s = seg.ident.to_string();
                if FORBIDDEN_IDENTS.contains(&s.as_str()) {
                    self.hits.push(s);
                }
            }
        }
        syn::visit::visit_path(self, p);
    }
}

#[test]
fn update_body_has_no_engine_connection_read() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");
    let file = syn::parse_file(&src).expect("parse src/main.rs");
    let mut v = UpdateBodyVisitor {
        in_update: false,
        hits: Vec::new(),
    };
    v.visit_file(&file);
    assert!(
        v.hits.is_empty(),
        "Flowsurface::update() must not access engine statics; found: {:?}",
        v.hits
    );
}
