//! F9e: 送信履歴 / バッファ削除 UI のソースインスペクション回帰テスト。
//!
//! `送信履歴を開く` と `バッファを削除…` の実装が main.rs に配線されていることを
//! ソース検査で確認する。

use std::fs;
use std::path::Path;

fn read_main_rs() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs");
    fs::read_to_string(&path).expect("failed to read src/main.rs")
}

fn read_native_menu_rs() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/native_menu.rs");
    fs::read_to_string(&path).expect("failed to read src/native_menu.rs")
}

// ---------------------------------------------------------------------------
// OpenSubmissionLog が native_menu.rs の Action enum に存在する
// ---------------------------------------------------------------------------

#[test]
fn open_submission_log_action_in_native_menu() {
    let src = read_native_menu_rs();
    assert!(
        src.contains("OpenSubmissionLog"),
        "native_menu.rs must define Action::OpenSubmissionLog"
    );
}

// ---------------------------------------------------------------------------
// ClearRunBuffer が native_menu.rs の Action enum に存在する
// ---------------------------------------------------------------------------

#[test]
fn clear_run_buffer_action_in_native_menu() {
    let src = read_native_menu_rs();
    assert!(
        src.contains("ClearRunBuffer"),
        "native_menu.rs must define Action::ClearRunBuffer"
    );
}

// ---------------------------------------------------------------------------
// main.rs が OpenSubmissionLog ハンドラを持つ
// ---------------------------------------------------------------------------

#[test]
fn open_submission_log_handler_in_main() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::OpenSubmissionLog"),
        "main.rs must handle Action::OpenSubmissionLog"
    );
}

// ---------------------------------------------------------------------------
// main.rs が ClearRunBuffer ハンドラを持ち confirm dialog を表示する
// ---------------------------------------------------------------------------

#[test]
fn clear_run_buffer_shows_confirm_dialog() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::ClearRunBuffer"),
        "main.rs must handle Action::ClearRunBuffer"
    );
    // 削除前に confirm dialog を表示することを確認
    assert!(
        src.contains("ClearRunBufferConfirmed"),
        "main.rs must show a confirm dialog before clearing the run buffer (ClearRunBufferConfirmed)"
    );
}

// ---------------------------------------------------------------------------
// ClearRunBufferConfirmed ハンドラが run_buffer を empty() にリセットする
// ---------------------------------------------------------------------------

#[test]
fn clear_run_buffer_confirmed_resets_index() {
    let src = read_main_rs();
    assert!(
        src.contains("Message::ClearRunBufferConfirmed"),
        "main.rs must handle Message::ClearRunBufferConfirmed"
    );
    // 削除後に run_buffer インデックスをリセットすることを確認
    assert!(
        src.contains("RunBufferIndex::empty()"),
        "ClearRunBufferConfirmed handler must reset run_buffer to RunBufferIndex::empty()"
    );
}

// ---------------------------------------------------------------------------
// バッファ削除後のトースト通知が存在する
// ---------------------------------------------------------------------------

#[test]
fn clear_run_buffer_shows_success_toast() {
    let src = read_main_rs();
    // 成功時のトーストを確認（日本語文言 or run-buffer 言及）
    let has_toast = src.contains("run-buffer を削除しました") || src.contains("削除しました");
    assert!(
        has_toast,
        "main.rs must show a success toast after clearing the run buffer"
    );
}
