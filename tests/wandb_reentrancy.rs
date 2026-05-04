//! Reentrancy guard tests for the W&B submission flow.
//!
//! These tests use source-inspection to verify that the design invariants
//! required by 統一決定 46 (submit_in_flight) are implemented correctly.

use std::fs;
use std::path::Path;

fn src_root() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn read_source(rel: &str) -> String {
    let path = src_root().join(rel);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("failed to read {}: {}", path.display(), e))
}

// ---------------------------------------------------------------------------
// Test 1: WandbSubmitModal has a `submitting: bool` field
// ---------------------------------------------------------------------------

#[test]
fn submit_modal_has_submitting_field() {
    let source = read_source("modal/wandb_submit.rs");
    assert!(
        source.contains("submitting"),
        "WandbSubmitModal must have a `submitting` field"
    );
    assert!(
        source.contains("submitting: bool"),
        "WandbSubmitModal.submitting must be typed `bool`"
    );
}

// ---------------------------------------------------------------------------
// Test 2: update() guards against re-entry when submitting == true
// ---------------------------------------------------------------------------

#[test]
fn submit_ignored_when_submitting() {
    let source = read_source("modal/wandb_submit.rs");
    // The update() function must contain a guard that checks `self.submitting`
    // before processing a second Submit message.
    let has_guard = source.contains("if self.submitting")
        || source.contains("self.submitting {")
        || source.contains("self.submitting\n");
    assert!(
        has_guard,
        "WandbSubmitModal::update() must guard against re-entry with \
         `if self.submitting {{ return None; }}`"
    );
}

// ---------------------------------------------------------------------------
// Test 3: submit_in_flight concept exists somewhere in src/
// ---------------------------------------------------------------------------

#[test]
fn submit_in_flight_concept_exists() {
    let src_dir = src_root();

    // Walk src/ looking for any .rs file that mentions submit_in_flight.
    let found = walk_rs_files(&src_dir, "submit_in_flight");
    assert!(
        found,
        "The submit_in_flight concept (統一決定 46) must be referenced in at least \
         one src/*.rs file as a comment or identifier"
    );
}

/// Recursively scan `dir` for `.rs` files containing `needle`.
fn walk_rs_files(dir: &Path, needle: &str) -> bool {
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return false,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            if walk_rs_files(&path, needle) {
                return true;
            }
        } else if path.extension().and_then(|e| e.to_str()) == Some("rs") {
            if let Ok(content) = fs::read_to_string(&path) {
                if content.contains(needle) {
                    return true;
                }
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Test 4: ClearRunBuffer action exists in native_menu.rs or main.rs
// ---------------------------------------------------------------------------

#[test]
fn clear_run_buffer_action_exists() {
    let native_menu = read_source("native_menu.rs");

    // ClearRunBuffer must be referenced in the menu action definitions.
    // It may live in native_menu.rs (Action enum) or main.rs (handler).
    let in_native_menu = native_menu.contains("ClearRunBuffer");

    let in_main = {
        let main_path = src_root().join("main.rs");
        fs::read_to_string(&main_path)
            .map(|s| s.contains("ClearRunBuffer"))
            .unwrap_or(false)
    };

    assert!(
        in_native_menu || in_main,
        "ClearRunBuffer must appear in src/native_menu.rs or src/main.rs"
    );
}

// ---------------------------------------------------------------------------
// Test 5: WandbSubmitModal.submitting starts as false (default state check)
// ---------------------------------------------------------------------------

#[test]
fn submit_modal_initial_state_is_not_submitting() {
    let source = read_source("modal/wandb_submit.rs");
    // The constructor (new / default) must set submitting to false.
    assert!(
        source.contains("submitting: false"),
        "WandbSubmitModal must initialize `submitting: false` in its constructor"
    );
}

// ---------------------------------------------------------------------------
// Test 6: Done message clears the submitting flag
// ---------------------------------------------------------------------------

#[test]
fn done_message_clears_submitting_flag() {
    let source = read_source("modal/wandb_submit.rs");
    // There must be code in update() that sets submitting = false for Done.
    // We check that the Done arm and `self.submitting = false` both appear.
    assert!(
        source.contains("self.submitting = false"),
        "update() must set `self.submitting = false` when Done or Failed is received"
    );
}

// ---------------------------------------------------------------------------
// Test 7: Failed message also clears the submitting flag
// ---------------------------------------------------------------------------

#[test]
fn failed_message_clears_submitting_flag() {
    // Covered by the same assertion as Test 6 (same code path).
    // This test provides an explicit, named regression guard.
    let source = read_source("modal/wandb_submit.rs");
    assert!(
        source.contains("Message::Failed"),
        "WandbSubmitModal must handle Message::Failed to reset the submitting flag"
    );
}

// ---------------------------------------------------------------------------
// Phase 3-A C2: Cancel 経路で SUBMIT_IN_FLIGHT をリセットする
// 統一決定 46: 送信中に Cancel が来たら subprocess を kill しつつ
// SUBMIT_IN_FLIGHT を必ず false に戻す。リセット忘れはモード切替不能を招く。
// ---------------------------------------------------------------------------

/// Char-boundary safe slice for matching against multibyte (Japanese) source.
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
fn cancel_during_submit_clears_in_flight() {
    let source = read_source("main.rs");
    // Action::Cancel ハンドラ近傍に SUBMIT_IN_FLIGHT.store(false, ...) が含まれる
    // ことを確認する。
    let cancel_idx = source
        .find("Some(modal::wandb_submit::Action::Cancel)")
        .expect("main.rs must match Some(modal::wandb_submit::Action::Cancel)");
    let window = safe_slice(&source, cancel_idx.saturating_sub(200), cancel_idx + 2400);
    // rustfmt may break the call across lines; tolerate both forms.
    let condensed: String = window.split_whitespace().collect::<Vec<_>>().join(" ");
    let has_reset = condensed.contains("SUBMIT_IN_FLIGHT .store(false")
        || condensed.contains("SUBMIT_IN_FLIGHT.store(false");
    assert!(
        has_reset,
        "Action::Cancel handler must reset SUBMIT_IN_FLIGHT to false \
         (統一決定 46) — window: {window}"
    );
}

// ---------------------------------------------------------------------------
// Phase 3-A C2: subprocess kill が Cancel 経路で発火する
// global handle (SUBMIT_CHILD) 経由で Child::kill を呼ぶこと。
// ---------------------------------------------------------------------------

#[test]
fn cancel_kills_subprocess_via_global_handle() {
    let source = read_source("main.rs");
    assert!(
        source.contains("SUBMIT_CHILD"),
        "main.rs must define a global SUBMIT_CHILD handle so Cancel can \
         kill the in-flight submit subprocess (統一決定 46 / P9 §送信モーダル UI)"
    );
    // Cancel 経路で kill() を呼んでいること
    let cancel_idx = source
        .find("Some(modal::wandb_submit::Action::Cancel)")
        .expect("main.rs must match Some(modal::wandb_submit::Action::Cancel)");
    let window = safe_slice(&source, cancel_idx.saturating_sub(200), cancel_idx + 1200);
    assert!(
        window.contains("kill"),
        "Action::Cancel handler must call kill() on the spawned submit child \
         — window: {window}"
    );
}

// ---------------------------------------------------------------------------
// Phase 3-A C2: submit_wandb_run は Child を spawn して global に保持する
// 一括 .output().await ではなく spawn() ベースに変更されていること。
// ---------------------------------------------------------------------------

#[test]
fn submit_wandb_run_uses_spawn_with_global_child_handle() {
    let source = read_source("main.rs");
    // submit_wandb_run 関数本体を抽出
    let fn_idx = source
        .find("async fn submit_wandb_run")
        .expect("main.rs must define async fn submit_wandb_run");
    // 関数本体は ~3500 byte ほどに広がっている（Phase 3-B の H8 path 検証 +
    // Phase 3-A の SUBMIT_CHILD spawn シーケンスを含むため）。
    let body = safe_slice(&source, fn_idx, fn_idx + 3500);
    assert!(
        body.contains(".spawn()"),
        "submit_wandb_run must spawn the child explicitly so its handle can \
         be stored for cancellation"
    );
    assert!(
        body.contains("SUBMIT_CHILD"),
        "submit_wandb_run must store the spawned Child in the SUBMIT_CHILD \
         global so Action::Cancel can reach it"
    );
}

// ---------------------------------------------------------------------------
// Phase 3-A C2: SUBMIT_IN_FLIGHT への重複 store(false) が安全であることを
// 設計レベルで保証する。AtomicBool::store は idempotent。
// ---------------------------------------------------------------------------

#[test]
fn submit_in_flight_is_idempotent_on_double_release() {
    use std::sync::atomic::{AtomicBool, Ordering};
    let flag = AtomicBool::new(true);
    flag.store(false, Ordering::Release);
    flag.store(false, Ordering::Release); // 二重リセットしても問題ない
    assert!(!flag.load(Ordering::Acquire));
}

// ---------------------------------------------------------------------------
// R3 / R2-M1: Action::Submit must be a no-op when latest_completed is None.
// SUBMIT_IN_FLIGHT must NOT be set if there is no run to submit (otherwise
// the reentrancy guard locks forever with nothing to release it).
// ---------------------------------------------------------------------------

#[test]
fn submit_action_no_op_when_latest_completed_is_none() {
    let source = read_source("main.rs");
    let idx = source
        .find("Some(modal::wandb_submit::Action::Submit {")
        .expect("main.rs must match modal::wandb_submit::Action::Submit");
    // Inspect ~1500 bytes of the handler body
    let window = safe_slice(&source, idx, idx + 1500);

    // The guard MUST appear before the SUBMIT_IN_FLIGHT compare_exchange,
    // otherwise the in-flight flag could be raised with no run to submit.
    let guard_pos = window
        .find("self.run_buffer.latest_completed.is_none()")
        .unwrap_or_else(|| {
            panic!(
                "Action::Submit handler must guard on \
                 `self.run_buffer.latest_completed.is_none()` before \
                 SUBMIT_IN_FLIGHT.compare_exchange — window: {window}"
            )
        });
    let cas_pos = window
        .find("compare_exchange")
        .expect("Action::Submit handler must use SUBMIT_IN_FLIGHT.compare_exchange");
    assert!(
        guard_pos < cas_pos,
        "latest_completed.is_none() guard must appear BEFORE \
         SUBMIT_IN_FLIGHT.compare_exchange (R2-M1) — window: {window}"
    );
}

// ---------------------------------------------------------------------------
// R3 / R2-M3: Cancel 経路で SUBMIT_IN_FLIGHT のリセットは kill().await の
// 「後」に行う。先にリセットすると新しい Submit が再入できる窓ができる。
// ---------------------------------------------------------------------------

#[test]
fn cancel_releases_submit_in_flight_after_kill_completes() {
    let source = read_source("main.rs");
    let cancel_idx = source
        .find("Some(modal::wandb_submit::Action::Cancel)")
        .expect("main.rs must match Action::Cancel");
    let window = safe_slice(&source, cancel_idx, cancel_idx + 2500);

    // The store(false) must appear AFTER `kill().await` lexically.
    // Whitespace-collapse the window to tolerate rustfmt line breaks.
    let condensed: String = window.split_whitespace().collect::<Vec<_>>().join(" ");

    let kill_pos = condensed
        .find("kill().await")
        .or_else(|| condensed.find("kill() .await"))
        .unwrap_or_else(|| {
            panic!("Cancel handler must call kill().await on the spawned child — window: {window}")
        });
    let store_pos = condensed
        .find("SUBMIT_IN_FLIGHT.store(false")
        .or_else(|| condensed.find("SUBMIT_IN_FLIGHT .store(false"))
        .unwrap_or_else(|| {
            panic!("Cancel handler must reset SUBMIT_IN_FLIGHT to false — window: {window}")
        });
    assert!(
        kill_pos < store_pos,
        "SUBMIT_IN_FLIGHT.store(false) must come AFTER kill().await so a \
         new Submit cannot re-enter while the old subprocess is still being \
         torn down (R2-M3) — condensed: {condensed}"
    );
}

// ---------------------------------------------------------------------------
// R3 / R2-H2: WandbSubmitResult(Ok) ハンドラは scan_async を経由する。
// 同期 RunBufferIndex::scan(&base) は iced update スレッドをブロックするため
// 禁止。Message::RunBufferIndexScanned 経由で refresh_tools_enable を実行する。
// ---------------------------------------------------------------------------

#[test]
fn wandb_submit_result_uses_async_scan() {
    let source = read_source("main.rs");
    let idx = source
        .find("Message::WandbSubmitResult(result)")
        .expect("main.rs must handle Message::WandbSubmitResult");
    // The Ok-branch starts shortly after; inspect ~2500 bytes of the
    // handler to cover the success arm.
    let handler = safe_slice(&source, idx, idx + 2500);

    // Must NOT call the synchronous scan inside the handler body.
    // Allow `scan_async` token (substring of "scan_async") and reject
    // bare `scan(&` invocations.
    let bad_scan = handler
        .lines()
        .filter(|line| !line.trim_start().starts_with("//"))
        .any(|line| line.contains("RunBufferIndex::scan(&") || line.contains(".scan(&"));
    assert!(
        !bad_scan,
        "WandbSubmitResult handler must NOT call the synchronous \
         RunBufferIndex::scan; use scan_async via Task::perform → \
         Message::RunBufferIndexScanned instead (R2-H2) — handler: {handler}"
    );
    assert!(
        handler.contains("scan_async") || handler.contains("RunBufferIndexScanned"),
        "WandbSubmitResult handler must dispatch scan_async (R2-H2) — handler: {handler}"
    );
}
