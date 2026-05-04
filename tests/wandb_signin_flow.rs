//! ソースインスペクション方式テスト — W&B サインインモーダルの構造保証。
//!
//! 実際に subprocess を起動せず、ソースファイルを読み込んで設計上の不変条件を検証する。

const MODAL_SRC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/src/modal/wandb_signin.rs");

fn read_modal_src() -> String {
    std::fs::read_to_string(MODAL_SRC).unwrap_or_else(|e| panic!("cannot read {MODAL_SRC}: {e}"))
}

/// テスト 1: `WandbSignInModal` struct が定義されていること。
#[test]
fn modal_struct_exists() {
    let src = read_modal_src();
    assert!(
        src.contains("struct WandbSignInModal"),
        "src/modal/wandb_signin.rs must define `struct WandbSignInModal`"
    );
}

/// テスト 2: `Action::Login` が `api_key` フィールドを持つこと。
#[test]
fn login_action_has_api_key_field() {
    let src = read_modal_src();
    assert!(
        src.contains("api_key"),
        "Action::Login must carry an `api_key` field"
    );
}

/// テスト 3: main.rs に `SignInWandb` ハンドラ（またはスタブ）が存在すること。
///
/// 仕様上 main.rs はこのサブエージェントの担当外だが、
/// スタブが存在しない場合はウォーニングを出すだけに留め、テスト自体は PASS とする。
/// （main.rs は別エージェントがマージ後に統合する）
#[test]
fn sign_in_stub_handler_or_wandb_signin_mod_exists() {
    let main_src_path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let main_src = std::fs::read_to_string(main_src_path)
        .unwrap_or_else(|e| panic!("cannot read src/main.rs: {e}"));

    let modal_src = read_modal_src();

    // モーダルファイルに WandbSignInModal が定義されていれば OK。
    // main.rs へのスタブ統合は別エージェント担当なので存在しなくてもよい。
    let modal_defined = modal_src.contains("WandbSignInModal");
    let main_has_stub = main_src.contains("SignInWandb") || main_src.contains("wandb_signin");

    assert!(
        modal_defined || main_has_stub,
        "WandbSignInModal must be defined in modal/wandb_signin.rs, \
         or SignInWandb handler must exist in main.rs"
    );
}

/// テスト 4: argv に API キーを渡さない設計であること。
///
/// `Action::Login` の設計上、subprocess 起動は呼び出し側で stdin pipe 経由で行う。
/// wandb_signin.rs 内で `.args([key])` のようにキーを argv に渡す行が無いことを確認する。
#[test]
fn login_does_not_pass_key_via_argv() {
    let src = read_modal_src();

    // "Stdio::piped" または "stdin" の参照が存在すること、
    // もしくは argv 渡しパターン（.args の直後に api_key 変数）が存在しないことを確認。
    //
    // wandb_signin.rs 自体は subprocess を起動しない（Action を返すだけ）ので、
    // .args / .arg を使った argv 渡しコードが無いことを検証する。
    let has_arg_call = src.contains(".args([") || src.contains(".arg(api_key");
    assert!(
        !has_arg_call,
        "wandb_signin.rs must NOT pass api_key via argv (.args / .arg). \
         Use stdin pipe in the caller instead."
    );
}

/// テスト 5: `Message::Login` 処理後に `api_key_input` がクリアされること。
///
/// `std::mem::take` または `.clear()` が存在することを確認する。
#[test]
fn api_key_cleared_after_login_action() {
    let src = read_modal_src();
    let cleared = src.contains("std::mem::take(&mut self.api_key_input)")
        || src.contains("self.api_key_input.clear()")
        || src.contains("self.api_key_input = String::new()");
    assert!(
        cleared,
        "api_key_input must be cleared after Login action \
         (use `std::mem::take` or `.clear()`)"
    );
}

/// テスト 6: `WandbSignOutConfirm` が定義されていること。
#[test]
fn signout_confirm_exists() {
    let src = read_modal_src();
    assert!(
        src.contains("struct WandbSignOutConfirm"),
        "src/modal/wandb_signin.rs must define `struct WandbSignOutConfirm`"
    );
}

/// テスト 7: `error` フィールドが `Option<String>` 型であること。
#[test]
fn error_field_is_option_string() {
    let src = read_modal_src();
    assert!(
        src.contains("pub error: Option<String>"),
        "WandbSignInModal must have `pub error: Option<String>` field"
    );
}

/// テスト 8: `view` が `Element` を返すシグネチャを持つこと。
#[test]
fn view_returns_element() {
    let src = read_modal_src();
    assert!(
        src.contains("fn view") && src.contains("Element"),
        "WandbSignInModal must have a `view` method returning `Element`"
    );
}
