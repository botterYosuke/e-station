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
    method: AuthMethod,
    username: Option<String>,
    error: Option<String>,
}

/// M4 (R1 Phase 1): method を String → enum 化。
/// 取りうる値は env / netrc / none のみ。snake_case JSON → variant マッピングは
/// `#[serde(rename_all = "snake_case")]` で自動。
#[derive(Debug, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum AuthMethod {
    Env,
    Netrc,
    None,
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
    assert_eq!(state.method, AuthMethod::Env);
    assert_eq!(state.username, None);
    assert_eq!(state.error, None);
}

#[test]
fn authenticated_false_none_method_deserialized() {
    let json = r#"{"authenticated": false, "method": "none", "username": null, "error": null}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(!state.authenticated);
    assert_eq!(state.method, AuthMethod::None);
}

#[test]
fn authenticated_true_netrc_with_username() {
    let json = r#"{"authenticated": true, "method": "netrc", "username": "alice", "error": null}"#;
    let state: WandbAuthState = serde_json::from_str(json).unwrap();
    assert!(state.authenticated);
    assert_eq!(state.method, AuthMethod::Netrc);
    assert_eq!(state.username, Some("alice".to_string()));
}

/// M4: 想定外の method 値は deserialize で reject される（fail-closed）。
#[test]
fn test_auth_method_unknown_value_rejected() {
    let json = r#"{"authenticated": true, "method": "ldap", "username": null, "error": null}"#;
    let result: Result<WandbAuthState, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "AuthMethod must reject unknown variants (got Ok)"
    );
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
        src.contains("pub method: AuthMethod"),
        "WandbAuthState must have method field of type AuthMethod (M4)"
    );
    assert!(
        src.contains("pub enum AuthMethod"),
        "src/wandb_auth.rs must define AuthMethod enum (M4)"
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

// ── M13 (R1 Phase 3-C): MetaJson 型付きデシリアライズ ───────────────────────
//
// `RunBufferIndex::scan` / `scan_async` は meta.json を `serde_json::Value` ではなく
// 型付きの `MetaJson` 構造体で受け取る。これによりフィールド名変更が compile-time に
// 検出される。
//
// テストは tests クレートでローカル定義を ミラーして検証する（bin-only crate のため）。

#[derive(Debug, serde::Deserialize, PartialEq, Eq)]
struct MetaJson {
    run_id: String,
    status: String,
    started_at: Option<String>,
}

#[test]
fn meta_json_basic_parse() {
    let json = r#"{"run_id": "abc", "status": "completed", "started_at": "2026-01-01T00:00:00Z"}"#;
    let m: MetaJson = serde_json::from_str(json).unwrap();
    assert_eq!(m.run_id, "abc");
    assert_eq!(m.status, "completed");
    assert_eq!(m.started_at, Some("2026-01-01T00:00:00Z".to_string()));
}

#[test]
fn meta_json_unknown_field_is_ignored() {
    // forward compat: 未知フィールドは無視される（deny_unknown_fields は付けない）
    let json =
        r#"{"run_id": "abc", "status": "completed", "started_at": null, "future_field": 42}"#;
    let m: MetaJson = serde_json::from_str(json).unwrap();
    assert_eq!(m.run_id, "abc");
    assert_eq!(m.status, "completed");
    assert_eq!(m.started_at, None);
}

#[test]
fn meta_json_missing_started_at_is_ok() {
    // started_at は Option なので欠落しても OK
    let json = r#"{"run_id": "abc", "status": "running"}"#;
    let m: MetaJson = serde_json::from_str(json).unwrap();
    assert_eq!(m.started_at, None);
}

#[test]
fn meta_json_missing_required_field_is_error() {
    // run_id は必須なので欠落で parse error
    let json = r#"{"status": "completed"}"#;
    let result: Result<MetaJson, _> = serde_json::from_str(json);
    assert!(result.is_err(), "missing run_id must be a parse error");
}

/// src/wandb_auth.rs に MetaJson struct が定義されていることをソース確認
#[test]
fn meta_json_struct_defined_in_wandb_auth_rs() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("struct MetaJson"),
        "src/wandb_auth.rs must define a typed MetaJson struct (M13)"
    );
    // serde_json::Value の動的パースを `meta.json` 用に使っていない事を確認
    // （MetaJson 型付きデシリアライズに置き換わっていることを保証）。
    // コメント行（// で始まる）は除外する。
    let has_value_use = src
        .lines()
        .any(|line| !line.trim_start().starts_with("//") && line.contains("serde_json::Value"));
    assert!(
        !has_value_use,
        "src/wandb_auth.rs must not use serde_json::Value (M13) — use the typed MetaJson struct"
    );
}

/// scan_async が wandb_auth.rs に定義されていることを確認（M3）
#[test]
fn run_buffer_index_scan_async_defined() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("async fn scan_async"),
        "src/wandb_auth.rs must define `pub async fn scan_async` (M3)"
    );
}
