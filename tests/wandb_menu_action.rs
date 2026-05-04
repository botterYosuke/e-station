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
