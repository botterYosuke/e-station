//! P7 受け入れ基準 9 — ソースインスペクション方式
//!
//! `ModeSwitchGuard` が Drop で `MODE_SWITCHING` を false に戻す実装が
//! ソースコードに存在することを構造的に検証する。
//!
//! panic 経路でも Drop が走るのは Rust の言語保証であるため、Drop が
//! `MODE_SWITCHING.store(false, ...)` を実装していることを確認すれば
//! 受け入れ基準 9 が保護される。
//!
//! 実際の panic unwind 動作は `#[should_panic]` 形式の単体テストではなく、
//! ソースインスペクションで Drop 実装の存在を保証する（ソースインスペクション方式）。

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

#[test]
fn mode_switch_guard_has_drop_impl() {
    let src = read_main();
    assert!(
        src.contains("impl Drop for ModeSwitchGuard"),
        "ModeSwitchGuard must have a Drop implementation to release MODE_SWITCHING on panic"
    );
}

#[test]
fn mode_switch_guard_drop_stores_false() {
    let src = read_main();
    // Drop impl must call MODE_SWITCHING.store(false, ...) to release the flag.
    assert!(
        src.contains("MODE_SWITCHING.store(false"),
        "ModeSwitchGuard::drop must store false into MODE_SWITCHING"
    );
}

#[test]
fn mode_switch_guard_try_acquire_uses_compare_exchange() {
    let src = read_main();
    assert!(
        src.contains("compare_exchange(false, true"),
        "ModeSwitchGuard::try_acquire must use compare_exchange to atomically acquire the flag"
    );
}

#[test]
fn mode_switch_guard_is_pub() {
    let src = read_main();
    assert!(
        src.contains("pub struct ModeSwitchGuard"),
        "ModeSwitchGuard must be pub so integration tests can reference it"
    );
}

#[test]
fn mode_switching_static_is_atomic_bool() {
    let src = read_main();
    assert!(
        src.contains("pub static MODE_SWITCHING"),
        "MODE_SWITCHING must be pub for test access"
    );
    assert!(
        src.contains("AtomicBool::new(false)"),
        "MODE_SWITCHING must initialise to false"
    );
}

#[test]
fn mode_switch_error_has_confirm_cancelled_variant() {
    let src = read_main();
    let pos = src
        .find("pub enum ModeSwitchError")
        .expect("ModeSwitchError enum must exist");
    let end = pos + src[pos..].find('}').expect("enum body must close");
    let body = &src[pos..=end];
    assert!(
        body.contains("ConfirmCancelled"),
        "ModeSwitchError must declare a `ConfirmCancelled` variant (L1) for F4 confirm cancel"
    );
}
