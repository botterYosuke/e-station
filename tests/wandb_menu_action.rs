/// F9c-menu: ソースインスペクションテスト
///
/// widget_menu_bar.rs / main.rs に Tools submenu 配線が実装されていることを
/// ファイル内容の grep で確認する。コンパイルを要しないため、
/// ビルド環境なしでも素早く実行できる。
use std::fs;

fn read_native_menu() -> String {
    fs::read_to_string("src/native_menu.rs").expect("src/native_menu.rs を読み込めません")
}

fn read_widget_menu_bar() -> String {
    fs::read_to_string("src/widget_menu_bar.rs").expect("src/widget_menu_bar.rs を読み込めません")
}

fn read_main_rs() -> String {
    fs::read_to_string("src/main.rs").expect("src/main.rs を読み込めません")
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

// ── widget_menu_bar Tools submenu 確認 ─────────────────────────────────────

#[test]
fn widget_menu_bar_has_tools_submenu_label() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("ツール（Tools）"),
        "widget_menu_bar must render Tools submenu with Japanese label"
    );
}

#[test]
fn widget_menu_bar_has_wandb_send_label() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("W&B に送信"),
        "widget_menu_bar Tools submenu must contain W&B 送信 item"
    );
}

#[test]
fn widget_menu_bar_maps_submit_to_wandb() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("Action::SubmitToWandb"),
        "widget_menu_bar must map Tools menu items to Action::SubmitToWandb"
    );
}

#[test]
fn widget_menu_bar_maps_sign_in_wandb() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("Action::SignInWandb"),
        "widget_menu_bar must map Tools menu items to Action::SignInWandb"
    );
}

#[test]
fn widget_menu_bar_maps_sign_out_wandb() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("Action::SignOutWandb"),
        "widget_menu_bar must map Tools menu items to Action::SignOutWandb"
    );
}

#[test]
fn widget_menu_bar_maps_open_submission_log() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("Action::OpenSubmissionLog"),
        "widget_menu_bar must map Tools menu items to Action::OpenSubmissionLog"
    );
}

#[test]
fn widget_menu_bar_maps_clear_run_buffer() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("Action::ClearRunBuffer"),
        "widget_menu_bar must map Tools menu items to Action::ClearRunBuffer"
    );
}

// ── widget_menu_bar enable/disable via tools_actions_for_state ─────────────

#[test]
fn widget_menu_bar_calls_tools_actions_for_state() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("tools_actions_for_state"),
        "widget_menu_bar must call tools_actions_for_state to compute enable/disable"
    );
}

#[test]
fn widget_menu_bar_uses_entry_enabled() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("enabled"),
        "widget_menu_bar must read MenuEntry.enabled from tools_actions_for_state result"
    );
}

// ── attach() signature includes WandbAuthState / RunBufferIndex ────────────

#[test]
fn attach_signature_includes_auth_and_buffer() {
    let src = read_native_menu();
    assert!(
        src.contains("WandbAuthState"),
        "native_menu.rs must reference WandbAuthState (attach signature)"
    );
    assert!(
        src.contains("RunBufferIndex"),
        "native_menu.rs must reference RunBufferIndex (attach signature)"
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

// ── main.rs handlers ─────────────────────────────────────────────────────

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
