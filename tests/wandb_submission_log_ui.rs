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

// ---------------------------------------------------------------------------
// Phase 3-A H6: 「ブラウザで開く」ボタンは Action::OpenUrl を dispatch する
// （Message::Done の再利用ではなく専用メッセージ Message::OpenUrl 経由）。
// ---------------------------------------------------------------------------

fn read_modal_rs() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/modal/wandb_submit.rs");
    fs::read_to_string(&path).expect("failed to read src/modal/wandb_submit.rs")
}

/// Char-boundary safe slice (modal-local copy; the H7 helper below is named
/// `safe_slice` and takes the same shape).
fn safe_slice_modal(src: &str, mut start: usize, mut end: usize) -> &str {
    if end > src.len() {
        end = src.len();
    }
    while start < src.len() && !src.is_char_boundary(start) {
        start += 1;
    }
    while end > start && !src.is_char_boundary(end) {
        end -= 1;
    }
    &src[start..end]
}

#[test]
fn open_url_button_dispatches_open_url_action_not_done() {
    let modal_src = read_modal_rs();
    // Message::OpenUrl(String) が Message enum に追加されている
    assert!(
        modal_src.contains("OpenUrl(String)"),
        "wandb_submit::Message must define OpenUrl(String) (H6)"
    );
    // 「ブラウザで開く」ボタンの on_press が Message::OpenUrl を使う。
    // コメント中の「ブラウザで開く」とぶつからないよう button() 呼び出しを直接探す。
    let btn_idx = modal_src
        .find("text(\"ブラウザで開く\")")
        .expect("modal must render a `text(\"ブラウザで開く\")` button");
    let window = safe_slice_modal(&modal_src, btn_idx, btn_idx + 400);
    assert!(
        window.contains("Message::OpenUrl"),
        "「ブラウザで開く」 button must dispatch Message::OpenUrl, not \
         Message::Done — window: {window}"
    );
    // Done を再利用していないこと
    assert!(
        !window.contains("Message::Done"),
        "「ブラウザで開く」 button must NOT reuse Message::Done — window: {window}"
    );

    // update() が Message::OpenUrl を Action::OpenUrl にマップする配線がある
    assert!(
        modal_src.contains("Message::OpenUrl"),
        "update() must handle Message::OpenUrl"
    );
}

// ---------------------------------------------------------------------------
// Phase 3-A H7: W&B 送信モーダルは main window 上にのみ描画される
// （popout window には描画されない）。
// ---------------------------------------------------------------------------

/// Safe slicing helper: clamps `start..end` to char boundaries so the slice
/// never panics on Japanese / multibyte characters in `src/main.rs`.
fn safe_slice(src: &str, mut start: usize, mut end: usize) -> &str {
    if end > src.len() {
        end = src.len();
    }
    while start < src.len() && !src.is_char_boundary(start) {
        start += 1;
    }
    while end > start && !src.is_char_boundary(end) {
        end -= 1;
    }
    &src[start..end]
}

#[test]
fn wandb_submit_modal_only_on_main_window() {
    let src = read_main_rs();
    // 構造: `let after_wandb_submit = if id == self.main_window.id { ... } else { after_replay_form };`
    let anchor = "let after_wandb_submit";
    let idx = src
        .find(anchor)
        .expect("main.rs must define `let after_wandb_submit = ...`");
    // 宣言式 (anchor から ~600 byte) に main-window guard が含まれること
    let snippet = safe_slice(&src, idx, idx + 800);
    assert!(
        snippet.contains("id == self.main_window.id"),
        "after_wandb_submit binding must guard the wandb_submit_modal overlay \
         with `id == self.main_window.id` so popout windows do not render the \
         modal (H7) — snippet: {snippet}"
    );
}

// ---------------------------------------------------------------------------
// M3 (R1 Phase 3-C): OpenSubmissionLog / ClearRunBufferConfirmed は
// 同期 I/O でブロックせず Task::perform 経由の async 経路を使う。
// ---------------------------------------------------------------------------

#[test]
fn open_submission_log_uses_async_scan() {
    let src = read_main_rs();
    let idx = src
        .find("Action::OpenSubmissionLog =>")
        .expect("main.rs must have an Action::OpenSubmissionLog handler");
    // ハンドラ本体を 1500 byte ほど切り出す
    let handler = safe_slice(&src, idx, idx + 1500);
    // 同期 scan は使わない（Task::perform 経由 or RunBufferIndex::scan_async を使う）
    assert!(
        handler.contains("scan_async") || handler.contains("Task::perform"),
        "Action::OpenSubmissionLog must use scan_async via Task::perform to avoid \
         blocking the iced update loop (M3) — handler: {handler}"
    );
}

#[test]
fn clear_run_buffer_confirmed_uses_tokio_remove_dir_all() {
    let src = read_main_rs();
    let idx = src
        .find("Message::ClearRunBufferConfirmed =>")
        .expect("main.rs must handle Message::ClearRunBufferConfirmed");
    let handler = safe_slice(&src, idx, idx + 1500);
    // 同期版 std::fs::remove_dir_all を update スレッドで直接呼ばない
    assert!(
        !handler.contains("std::fs::remove_dir_all"),
        "ClearRunBufferConfirmed handler must NOT use std::fs::remove_dir_all \
         (blocking I/O on iced update loop) — use tokio::fs::remove_dir_all via Task::perform (M3) — handler: {handler}"
    );
    assert!(
        handler.contains("tokio::fs::remove_dir_all") || handler.contains("Task::perform"),
        "ClearRunBufferConfirmed handler must use tokio::fs::remove_dir_all via \
         Task::perform (M3) — handler: {handler}"
    );
}

#[test]
fn wandb_signin_modal_only_on_main_window() {
    let src = read_main_rs();
    // 構造: `let after_signin = if id == self.main_window.id { if let Some(signin) = ... } else { after_wandb_submit };`
    // または最終 expression として直接 main_window ガードする。
    let anchor = "if let Some(signin) = &self.wandb_signin_modal";
    let idx = src
        .find(anchor)
        .expect("main.rs must build wandb_signin_modal overlay in view()");
    // anchor の直前 ~500 byte に main-window guard が出てくること
    let start = idx.saturating_sub(500);
    let snippet = safe_slice(&src, start, idx);
    assert!(
        snippet.contains("id == self.main_window.id"),
        "wandb_signin_modal overlay must be guarded by `id == self.main_window.id` \
         within ~500 bytes preceding the `if let Some(signin) = ...` block (H7) \
         — snippet: {snippet}"
    );
}
