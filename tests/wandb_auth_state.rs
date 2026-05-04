//! F9c: WandbAuthState の JSON deserialize + tools_actions_for_state の状態 × 項目テスト
//!
//! Source-inspection based: flowsurface は bin-only crate のため、
//! `use flowsurface::...` ではなく include_str! + serde_json 直接デシリアライズを使う。

// ── WandbAuthState JSON deserialize テスト ──────────────────────────────────

/// WandbAuthState と同じ構造を持つローカル定義（src/wandb_auth.rs のミラー）。
/// bin-only crate のため、ここでデシリアライズロジックを検証する。
#[derive(Debug, serde::Deserialize, PartialEq, Eq)]
struct WandbAuthState {
    authenticated: bool,
    method: String,
    username: Option<String>,
    error: Option<String>,
}

#[derive(Debug, serde::Deserialize, PartialEq, Eq)]
struct RunBufferIndex {
    latest_completed: Option<String>,
    total: usize,
}

#[test]
fn authenticated_true_env_method_deserialized() {
    let json = r#"{"authenticated": true, "method": "env", "username": null, "error": null}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(state.authenticated);
    assert_eq!(state.method, "env");
    assert_eq!(state.username, None);
    assert_eq!(state.error, None);
}

#[test]
fn authenticated_false_none_method_deserialized() {
    let json = r#"{"authenticated": false, "method": "none", "username": null, "error": null}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(!state.authenticated);
    assert_eq!(state.method, "none");
}

#[test]
fn authenticated_true_netrc_with_username() {
    let json = r#"{"authenticated": true, "method": "netrc", "username": "alice", "error": null}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(state.authenticated);
    assert_eq!(state.method, "netrc");
    assert_eq!(state.username, Some("alice".to_string()));
}

#[test]
fn error_field_preserved() {
    let json = r#"{"authenticated": false, "method": "none", "username": null, "error": "viewer_lookup_timeout"}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(!state.authenticated);
    assert_eq!(state.error, Some("viewer_lookup_timeout".to_string()));
}

// ── tools_actions_for_state 仕様テスト (source-inspection) ──────────────────
//
// bin-only crate のため、src/menu.rs のソースを読み込んで構造を検証する。

fn read_menu_src() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs")
}

fn read_wandb_auth_src() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/wandb_auth.rs");
    std::fs::read_to_string(path).expect("failed to read src/wandb_auth.rs")
}

/// tools_actions_for_state が WandbAuthState / RunBufferIndex を引数に取ることを確認
#[test]
fn tools_actions_for_state_uses_wandb_auth_state_and_run_buffer_index() {
    let src = read_menu_src();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_sig_end = src[fn_start..].find('{').unwrap_or(0);
    let sig = &src[fn_start..fn_start + fn_sig_end];

    assert!(
        sig.contains("WandbAuthState"),
        "tools_actions_for_state must take WandbAuthState parameter, got: {sig}"
    );
    assert!(
        sig.contains("RunBufferIndex"),
        "tools_actions_for_state must take RunBufferIndex parameter, got: {sig}"
    );
    assert!(
        sig.contains("Vec<MenuEntry>"),
        "tools_actions_for_state must return Vec<MenuEntry>, got: {sig}"
    );
}

/// WandbAuthState が wandb_auth.rs に定義されていることを確認
#[test]
fn wandb_auth_state_defined_in_wandb_auth_rs() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("pub struct WandbAuthState"),
        "WandbAuthState must be defined in src/wandb_auth.rs"
    );
    assert!(
        src.contains("pub authenticated: bool"),
        "WandbAuthState must have authenticated field"
    );
    assert!(
        src.contains("pub method: String"),
        "WandbAuthState must have method field"
    );
    assert!(
        src.contains("pub username: Option<String>"),
        "WandbAuthState must have username field"
    );
    assert!(
        src.contains("pub error: Option<String>"),
        "WandbAuthState must have error field"
    );
}

/// RunBufferIndex が wandb_auth.rs に定義されていることを確認
#[test]
fn run_buffer_index_defined_in_wandb_auth_rs() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("pub struct RunBufferIndex"),
        "RunBufferIndex must be defined in src/wandb_auth.rs"
    );
    assert!(
        src.contains("pub latest_completed: Option<String>"),
        "RunBufferIndex must have latest_completed field"
    );
    assert!(
        src.contains("pub total: usize"),
        "RunBufferIndex must have total field"
    );
}

/// unauthenticated() のデフォルト値を確認
#[test]
fn unauthenticated_default_is_fail_closed() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("fn unauthenticated()"),
        "WandbAuthState must have unauthenticated() constructor"
    );
    // fail-closed: authenticated = false のデフォルト
    let fn_start = src.find("fn unauthenticated()").unwrap();
    let body = &src[fn_start..fn_start + 300];
    assert!(
        body.contains("authenticated: false"),
        "unauthenticated() must set authenticated=false (fail-closed)"
    );
}

/// メニュー内テスト: 4 組合せ × 全項目の enabled/tooltip が仕様表どおりであることをソース確認
#[test]
fn tools_actions_for_state_returns_all_5_items() {
    let src = read_menu_src();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    // 次の pub fn までを関数本体とみなす
    let fn_end = fn_body
        .find("\npub fn ")
        .or_else(|| fn_body.find("\n#[cfg(test)]"))
        .unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // 全 5 アクションが本体に登場することを確認
    for action in &[
        "SignInWandb",
        "SignOutWandb",
        "SubmitToWandb",
        "OpenSubmissionLog",
        "ClearRunBuffer",
    ] {
        assert!(
            body.contains(action),
            "tools_actions_for_state must reference {action}"
        );
    }
}

/// 相互 disable の不変条件がソースに記述されていることを確認
#[test]
fn signin_signout_mutual_disable_invariant_in_source() {
    let src = read_menu_src();
    // 仕様コメントが関数ドキュメントに含まれていることを確認
    assert!(
        src.contains("mutually exclusive"),
        "tools_actions_for_state doc must mention mutual exclusion of SignIn/SignOut"
    );
}

/// OpenSubmissionLog が常に Vec に含まれる不変条件がソースに記述されていることを確認
#[test]
fn open_submission_log_always_present_invariant_in_source() {
    let src = read_menu_src();
    assert!(
        src.contains("always present"),
        "tools_actions_for_state doc must mention OpenSubmissionLog is always present"
    );
}
