//! Structural guard for invariant **T35-H9-SingleRecoveryPath**.
//!
//! `Flowsurface::subscription()` must wire the engine-status / engine-
//! connected stream through exactly one `Subscription::run(...)` call.
//! Splitting the recovery path into two subscriptions reintroduces the
//! "manual reconnect callback" duplication that H9 is meant to remove.
//!
//! See `docs/plan/tachibana/implementation-plan-T3.5.md` §3 Step A.

use syn::visit::Visit;

struct SubscriptionRunCounter {
    in_subscription: bool,
    runs: usize,
}

impl<'ast> Visit<'ast> for SubscriptionRunCounter {
    fn visit_impl_item_fn(&mut self, i: &'ast syn::ImplItemFn) {
        let was = self.in_subscription;
        if i.sig.ident == "subscription" {
            self.in_subscription = true;
        }
        syn::visit::visit_impl_item_fn(self, i);
        self.in_subscription = was;
    }

    fn visit_expr_call(&mut self, c: &'ast syn::ExprCall) {
        if self.in_subscription
            && let syn::Expr::Path(p) = c.func.as_ref()
        {
            let segs: Vec<String> = p
                .path
                .segments
                .iter()
                .map(|s| s.ident.to_string())
                .collect();
            if segs.last().is_some_and(|s| s == "run") && segs.iter().any(|s| s == "Subscription") {
                self.runs += 1;
            }
        }
        syn::visit::visit_expr_call(self, c);
    }
}

#[test]
fn engine_status_subscription_is_singleton() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");
    let file = syn::parse_file(&src).expect("parse src/main.rs");
    let mut v = SubscriptionRunCounter {
        in_subscription: false,
        runs: 0,
    };
    v.visit_file(&file);
    assert_eq!(
        v.runs, 1,
        "Flowsurface::subscription() must contain exactly one Subscription::run(...) \
         (engine recovery path), found {}",
        v.runs
    );
}
