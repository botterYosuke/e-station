//! Burn-in guard: `EngineConnection`'s `Debug` implementation must never
//! expose credential-shaped fields. The current struct stores only
//! channel handles (no auth token / password / session URLs), and the
//! manual `Debug` impl uses `debug_struct(...).finish_non_exhaustive()`
//! so future field additions do not silently leak through derive
//! propagation.
//!
//! See `docs/plan/✅tachibana/implementation-plan-T3.5.md` §3 Step A
//! REFACTOR ("secret 焼付きガード").

use std::path::PathBuf;

fn read_connection_rs() -> String {
    let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    path.push("src/connection.rs");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {}", path.display(), e))
}

#[test]
fn engine_connection_debug_does_not_leak_credentials() {
    let src = read_connection_rs();

    // The Debug impl must emit `EngineConnection { .. }` and not expand
    // any field — `finish_non_exhaustive()` is the load-bearing call.
    assert!(
        src.contains(r#"debug_struct("EngineConnection")"#),
        "EngineConnection must keep a manual Debug impl named via debug_struct"
    );
    assert!(
        src.contains("finish_non_exhaustive()"),
        "EngineConnection's Debug impl must call finish_non_exhaustive() so future \
         fields are not silently rendered (could leak credentials added later)"
    );

    // Defence-in-depth: the struct itself must not gain credential-shaped
    // fields without an explicit owner. `secrecy::SecretString` is the
    // approved wrapper; raw `token: String` etc. is forbidden.
    let lower = src.to_lowercase();
    for forbidden in [
        "token: string",
        "password: string",
        "secret: string",
        "api_key: string",
        "session_id: string",
    ] {
        assert!(
            !lower.contains(forbidden),
            "engine-client/src/connection.rs must not store credentials as plain \
             String — found pattern '{forbidden}'. Wrap with secrecy::SecretString \
             or move into a dedicated module before merging."
        );
    }
}
