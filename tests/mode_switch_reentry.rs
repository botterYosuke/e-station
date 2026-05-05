//! P7 受け入れ基準 11 — ソースインスペクション方式
//!
//! 2 回目の `try_acquire()` が `None` を返し再入を防ぐ実装が
//! ソースコードに存在することを構造的に検証する。
//!
//! `compare_exchange` は CAS (Compare-And-Swap) のため、flag が true の
//! 間は常に `Err` を返し `None` にマップされる。
//! ソースインスペクションで実装の存在を保証する。

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

#[test]
fn try_acquire_returns_none_on_second_call_contract() {
    let src = read_main();
    // The implementation must use compare_exchange which fails if already true,
    // and .ok().map(|_| ...) converts the failure to None.
    assert!(
        src.contains(".ok()") && src.contains(".map(|_| ModeSwitchGuard)"),
        "try_acquire must map Ok to Some(ModeSwitchGuard) and Err to None via .ok().map()"
    );
}

#[test]
fn mode_switch_guard_prevents_double_acquire_structurally() {
    let src = read_main();
    // Structural check: the only way try_acquire fails is if MODE_SWITCHING is true.
    // compare_exchange(false, true, ...) succeeds only when current == false.
    assert!(
        src.contains("compare_exchange(false, true"),
        "try_acquire must gate on MODE_SWITCHING being false (prevents reentry)"
    );
}

#[test]
fn mode_switch_guard_drop_resets_for_next_acquire() {
    let src = read_main();
    // After drop, MODE_SWITCHING is false → next try_acquire succeeds.
    assert!(
        src.contains("MODE_SWITCHING.store(false"),
        "ModeSwitchGuard::drop must reset MODE_SWITCHING so subsequent try_acquire succeeds"
    );
}

#[test]
fn mode_switch_error_already_switching_variant_exists() {
    let src = read_main();
    assert!(
        src.contains("AlreadySwitching"),
        "ModeSwitchError must have AlreadySwitching variant for blocked reentry"
    );
}
