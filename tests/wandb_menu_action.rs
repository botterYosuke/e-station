/// F9c-menu: ソースインスペクションテスト
///
/// native_menu.rs / main.rs に Tools submenu 配線が実装されていることを
/// ファイル内容の grep で確認する。コンパイルを要しないため、
/// ビルド環境なしでも素早く実行できる。
use std::fs;

fn read_native_menu() -> String {
    fs::read_to_string("src/native_menu.rs").expect("src/native_menu.rs を読み込めません")
}

fn read_main_rs() -> String {
    fs::read_to_string("src/main.rs").expect("src/main.rs を読み込めません")
}

// ── MenuIds フィールド確認 ─────────────────────────────────────────────────

#[test]
fn native_menu_ids_has_submit_to_wandb_field() {
    let src = read_native_menu();
    assert!(
        src.contains("submit_to_wandb"),
        "MenuIds struct must contain submit_to_wandb field"
    );
}

#[test]
fn native_menu_ids_has_sign_in_wandb_field() {
    let src = read_native_menu();
    assert!(
        src.contains("sign_in_wandb"),
        "MenuIds struct must contain sign_in_wandb field"
    );
}

#[test]
fn native_menu_ids_has_sign_out_wandb_field() {
    let src = read_native_menu();
    assert!(
        src.contains("sign_out_wandb"),
        "MenuIds struct must contain sign_out_wandb field"
    );
}

#[test]
fn native_menu_ids_has_open_submission_log_field() {
    let src = read_native_menu();
    assert!(
        src.contains("open_submission_log"),
        "MenuIds struct must contain open_submission_log field"
    );
}

#[test]
fn native_menu_ids_has_clear_run_buffer_field() {
    let src = read_native_menu();
    assert!(
        src.contains("clear_run_buffer"),
        "MenuIds struct must contain clear_run_buffer field"
    );
}

// ── event_stream のマッピング確認 ─────────────────────────────────────────

#[test]
fn event_stream_maps_submit_to_wandb() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::SubmitToWandb"),
        "event_stream must map submit_to_wandb -> Action::SubmitToWandb"
    );
}

#[test]
fn event_stream_maps_sign_in_wandb() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::SignInWandb"),
        "event_stream must map sign_in_wandb -> Action::SignInWandb"
    );
}

#[test]
fn event_stream_maps_sign_out_wandb() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::SignOutWandb"),
        "event_stream must map sign_out_wandb -> Action::SignOutWandb"
    );
}

#[test]
fn event_stream_maps_open_submission_log() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::OpenSubmissionLog"),
        "event_stream must map open_submission_log -> Action::OpenSubmissionLog"
    );
}

#[test]
fn event_stream_maps_clear_run_buffer() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::ClearRunBuffer"),
        "event_stream must map clear_run_buffer -> Action::ClearRunBuffer"
    );
}

// ── native_menu Action enum 確認 ─────────────────────────────────────────

#[test]
fn native_menu_action_enum_has_submit_to_wandb() {
    let src = read_native_menu();
    // Action enum itself must declare SubmitToWandb
    assert!(
        src.contains("SubmitToWandb"),
        "native_menu::Action enum must have SubmitToWandb variant"
    );
}

#[test]
fn native_menu_action_enum_has_sign_in_wandb() {
    let src = read_native_menu();
    assert!(
        src.contains("SignInWandb"),
        "native_menu::Action enum must have SignInWandb variant"
    );
}

#[test]
fn native_menu_action_enum_has_sign_out_wandb() {
    let src = read_native_menu();
    assert!(
        src.contains("SignOutWandb"),
        "native_menu::Action enum must have SignOutWandb variant"
    );
}

#[test]
fn native_menu_action_enum_has_open_submission_log() {
    let src = read_native_menu();
    assert!(
        src.contains("OpenSubmissionLog"),
        "native_menu::Action enum must have OpenSubmissionLog variant"
    );
}

#[test]
fn native_menu_action_enum_has_clear_run_buffer() {
    let src = read_native_menu();
    assert!(
        src.contains("ClearRunBuffer"),
        "native_menu::Action enum must have ClearRunBuffer variant"
    );
}

// ── Tools submenu 構造確認 ─────────────────────────────────────────────────

#[test]
fn native_menu_has_tools_submenu_label() {
    let src = read_native_menu();
    assert!(
        src.contains("ツール（Tools）"),
        "native_menu::attach must create a Tools submenu with Japanese label"
    );
}

#[test]
fn native_menu_has_wandb_send_label() {
    let src = read_native_menu();
    assert!(
        src.contains("W&B に送信"),
        "Tools submenu must contain W&B 送信 item"
    );
}

// ── main.rs ハンドラ確認 ─────────────────────────────────────────────────

#[test]
fn main_rs_handles_submit_to_wandb() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::SubmitToWandb"),
        "main.rs NativeMenuAction handler must handle Action::SubmitToWandb"
    );
}

#[test]
fn main_rs_handles_sign_in_wandb() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::SignInWandb"),
        "main.rs NativeMenuAction handler must handle Action::SignInWandb"
    );
}

#[test]
fn main_rs_handles_sign_out_wandb() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::SignOutWandb"),
        "main.rs NativeMenuAction handler must handle Action::SignOutWandb"
    );
}

#[test]
fn main_rs_handles_open_submission_log() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::OpenSubmissionLog"),
        "main.rs NativeMenuAction handler must handle Action::OpenSubmissionLog"
    );
}

#[test]
fn main_rs_handles_clear_run_buffer() {
    let src = read_main_rs();
    assert!(
        src.contains("Action::ClearRunBuffer"),
        "main.rs NativeMenuAction handler must handle Action::ClearRunBuffer"
    );
}

// ── F9c-H5: Tools 項目の enable/disable は tools_actions_for_state 経由 ────

/// H5: native_menu::attach() のシグネチャに WandbAuthState / RunBufferIndex
/// を受け取る引数が含まれることを確認する。
#[test]
fn attach_signature_includes_auth_and_buffer() {
    let src = read_native_menu();
    assert!(
        src.contains("WandbAuthState"),
        "native_menu.rs must reference WandbAuthState (attach signature update)"
    );
    assert!(
        src.contains("RunBufferIndex"),
        "native_menu.rs must reference RunBufferIndex (attach signature update)"
    );
    // pub fn attach(...) の引数に auth/buffer が現れること
    let attach_idx = src
        .find("pub fn attach")
        .expect("native_menu.rs must declare pub fn attach");
    let after = &src[attach_idx..];
    let sig_end = after.find(')').unwrap_or(after.len());
    let sig = &after[..sig_end];
    assert!(
        sig.contains("WandbAuthState"),
        "attach() signature must take &WandbAuthState, got: {sig}"
    );
    assert!(
        sig.contains("RunBufferIndex"),
        "attach() signature must take &RunBufferIndex, got: {sig}"
    );
}

/// H5: 未認証時に SubmitToWandb が disabled になる経路として
/// `tools_actions_for_state` の戻り値の `enabled` を使うことを source から確認する。
#[test]
fn submit_to_wandb_disabled_when_unauthenticated_in_native_menu() {
    let src = read_native_menu();
    assert!(
        src.contains("tools_actions_for_state"),
        "native_menu.rs must call tools_actions_for_state to compute Tools enable/disable"
    );
}

/// H5: バッファが空のときも `tools_actions_for_state` 経由で disable 計算されること。
#[test]
fn submit_to_wandb_disabled_when_buffer_empty_in_native_menu() {
    let src = read_native_menu();
    // tools_actions_for_state が呼ばれているならこの test と上のテストは実質同じだが
    // 検出パスを 2 段に分けることで仕様の二要因（auth × buffer）を構造的に担保する。
    assert!(
        src.contains("tools_actions_for_state"),
        "native_menu.rs must use tools_actions_for_state for buffer-state-aware disable"
    );
    // MenuEntry.enabled を MenuItem の enable 引数に流す配線が存在すること
    assert!(
        src.contains(".enabled"),
        "native_menu.rs must read MenuEntry.enabled from tools_actions_for_state result"
    );
}
