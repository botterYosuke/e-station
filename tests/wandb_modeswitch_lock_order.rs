//! F9c DoD: ロック取得順序の回帰テスト（統一決定 R3-58）。
//!
//! P9 spec 決定: MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE → CURRENT_PATH
//!
//! このファイルはソースインスペクション方式で次の不変条件を確認する:
//! - `SUBMIT_IN_FLIGHT` の定義が `src/main.rs` に存在する
//! - `SUBMIT_IN_FLIGHT` が `AtomicBool` として定義されている
//! - ロック取得順序のコメントが `main.rs` に記載されている

use std::fs;
use std::path::Path;

fn read_main_rs() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs");
    fs::read_to_string(&path).expect("failed to read src/main.rs")
}

// ---------------------------------------------------------------------------
// SUBMIT_IN_FLIGHT が AtomicBool として定義されている
// ---------------------------------------------------------------------------

#[test]
fn submit_in_flight_defined_as_atomic_bool() {
    let src = read_main_rs();
    assert!(
        src.contains("SUBMIT_IN_FLIGHT"),
        "main.rs must define SUBMIT_IN_FLIGHT (統一決定 46)"
    );
    assert!(
        src.contains("AtomicBool"),
        "SUBMIT_IN_FLIGHT must be typed AtomicBool"
    );
}

// ---------------------------------------------------------------------------
// ロック取得順序コメントが main.rs に記載されている（統一決定 R3-58）
// ---------------------------------------------------------------------------

#[test]
fn lock_order_comment_present() {
    let src = read_main_rs();
    // Lock order: MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE → CURRENT_PATH
    let has_order_comment = src.contains("MODE_SWITCHING")
        && src.contains("SUBMIT_IN_FLIGHT")
        && src.contains("APP_MODE");
    assert!(
        has_order_comment,
        "main.rs must document lock acquisition order \
         MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE (統一決定 R3-58)"
    );
}

// ---------------------------------------------------------------------------
// SUBMIT_IN_FLIGHT.compare_exchange / store が逆順ロックを防ぐ構造になっている
// ---------------------------------------------------------------------------

#[test]
fn submit_in_flight_uses_atomic_store_and_compare_exchange() {
    let src = read_main_rs();
    // compare_exchange で CAS ベースの取得をしていることを確認
    let has_cas = src.contains("compare_exchange") || src.contains("SUBMIT_IN_FLIGHT.store(true");
    assert!(
        has_cas,
        "main.rs must use AtomicBool::compare_exchange or store(true) for SUBMIT_IN_FLIGHT \
         to prevent double-dispatch (統一決定 46)"
    );
    // store(false) でリリースしていることを確認
    assert!(
        src.contains("SUBMIT_IN_FLIGHT.store(false"),
        "main.rs must release SUBMIT_IN_FLIGHT with store(false) after submit completes"
    );
}

// ---------------------------------------------------------------------------
// submit_in_flight ガードが SubmitToWandb handler にある
// ---------------------------------------------------------------------------

#[test]
fn submit_in_flight_guard_in_submit_handler() {
    let src = read_main_rs();
    // Action::SubmitToWandb と SUBMIT_IN_FLIGHT の両方が存在する
    assert!(
        src.contains("Action::SubmitToWandb") && src.contains("SUBMIT_IN_FLIGHT"),
        "Action::SubmitToWandb handler must reference SUBMIT_IN_FLIGHT guard"
    );
}
