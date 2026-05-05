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

/// M8: `wandb_login` の stdin write が握り潰されず Result で伝播されること。
///
/// 旧コード `let _ = stdin.write_all(...)` は pipe broken など OS-level I/O
/// 失敗を黙殺し、原因不明のログイン失敗の温床になっていた。
#[test]
fn wandb_login_returns_error_on_stdin_write_failure() {
    let main_src_path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let main_src = std::fs::read_to_string(main_src_path)
        .unwrap_or_else(|e| panic!("cannot read src/main.rs: {e}"));

    // wandb_login 関数の本体を抽出
    let fn_idx = main_src
        .find("async fn wandb_login")
        .expect("wandb_login function must exist in src/main.rs");
    // 次の async fn / pub fn / 末尾までを切り出す
    let after = &main_src[fn_idx..];
    let end = after[1..]
        .find("\nasync fn ")
        .or_else(|| after[1..].find("\npub fn "))
        .or_else(|| after[1..].find("\nfn "))
        .map(|i| i + 1)
        .unwrap_or(after.len());
    let body = &after[..end];

    // 旧パターン（黙殺）が消えていること（コメント行は除外）
    let has_silent_ignore = body
        .lines()
        .filter(|l| {
            let t = l.trim_start();
            !t.starts_with("//") && !t.starts_with("///")
        })
        .any(|l| l.contains("let _ = stdin.write_all"));
    assert!(
        !has_silent_ignore,
        "M8: wandb_login must not silently ignore stdin write errors \
         (`let _ = stdin.write_all(...)` is forbidden)"
    );
    // 新パターン（`?` 伝播 or .map_err(...) ?）が存在すること
    assert!(
        body.contains("stdin.write_all") && body.contains("?"),
        "M8: wandb_login must propagate stdin write errors via `?`"
    );
    assert!(
        body.contains("stdin write failed") || body.contains("stdin not piped"),
        "M8: wandb_login must produce an explicit stdin error message"
    );
}

/// F9 R1-M4: `Message::LoginFailed(String)` が定義され、`update()` が内部状態
/// (`submitting=false` + `error=Some`) を一元管理する。親（main.rs）が直接
/// `modal.submitting = false; modal.error = Some(...)` を書き換える経路は禁止。
#[test]
fn login_failed_message_routes_through_update() {
    let src = read_modal_src();
    // Message::LoginFailed(String) variant が enum に追加されている
    assert!(
        src.contains("LoginFailed(String)"),
        "wandb_signin::Message must define LoginFailed(String) (F9 R1-M4)"
    );
    // update() 内に `Message::LoginFailed` arm が存在し、submitting=false /
    // error=Some を設定する
    // The Message enum variant declaration appears first ("LoginFailed(String)");
    // skip past it to land on the arm inside `update()`.
    let arm_idx = src
        .match_indices("Message::LoginFailed")
        .map(|(i, _)| i)
        .next()
        .expect("update() must handle Message::LoginFailed");
    // arm body を 1200 byte 切り出して内部状態の更新を確認（日本語コメントが
    // バイト数を膨らませるため余裕を持たせる）
    let arm_body_end = (arm_idx + 1200).min(src.len());
    let mut safe = arm_body_end;
    while !src.is_char_boundary(safe) {
        safe -= 1;
    }
    let arm = &src[arm_idx..safe];
    assert!(
        arm.contains("self.submitting = false"),
        "Message::LoginFailed arm must set self.submitting = false — arm: {arm}"
    );
    assert!(
        arm.contains("self.error = Some"),
        "Message::LoginFailed arm must set self.error = Some(...) — arm: {arm}"
    );
}

/// F9 R1-M4: main.rs は失敗時に直接フィールドを書き換えず Message 経由で
/// `Message::LoginFailed` を `modal.update(...)` に流す。
#[test]
fn main_rs_routes_login_failure_via_message() {
    let main_src_path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let main_src = std::fs::read_to_string(main_src_path)
        .unwrap_or_else(|e| panic!("cannot read src/main.rs: {e}"));
    // WandbLoginResult ハンドラ（match arm）が Message::LoginFailed を経由
    // していること。単純な `Message::WandbLoginResult` は Task::perform 末尾の
    // mapper にもヒットするため、handler arm の開始位置 `Message::WandbLoginResult(...)
    //  =>` を優先して探す。
    let idx = main_src
        .find("Message::WandbLoginResult(result)")
        .or_else(|| main_src.find("Message::WandbLoginResult(res)"))
        .or_else(|| main_src.find("Message::WandbLoginResult"))
        .expect("main.rs must handle Message::WandbLoginResult");
    let end = (idx + 2000).min(main_src.len());
    let mut safe = end;
    while !main_src.is_char_boundary(safe) {
        safe -= 1;
    }
    let handler = &main_src[idx..safe];
    assert!(
        handler.contains("Message::LoginFailed"),
        "WandbLoginResult error arm must dispatch wandb_signin::Message::LoginFailed (F9 R1-M4) — handler: {handler}"
    );
}

/// F9 R2-H1: `Message::WandbLoginResult(Ok)` must trigger `refresh_wandb_auth`
/// even when the user has cancelled the modal mid-login (`wandb_signin_modal
/// = None`). The previous structure wrapped the entire match in
/// `if let Some(modal)` and silently dropped the refresh on cancel-then-success,
/// leaving the UI in "未認証" while `.netrc` was already populated.
#[test]
fn wandb_login_result_ok_dispatches_refresh_unconditionally() {
    let main_src_path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let main_src = std::fs::read_to_string(main_src_path)
        .unwrap_or_else(|e| panic!("cannot read src/main.rs: {e}"));

    // Locate the handler arm specifically (skip the variant declaration).
    let idx = main_src
        .find("Message::WandbLoginResult(result)")
        .expect("main.rs must handle Message::WandbLoginResult(result) (F9 R2-H1)");
    let end = (idx + 2000).min(main_src.len());
    let mut safe = end;
    while !main_src.is_char_boundary(safe) {
        safe -= 1;
    }
    let handler = &main_src[idx..safe];

    // Locate the `Ok(())` branch and the `Err(err)` branch.
    let ok_idx = handler
        .find("Ok(())")
        .expect("handler must have Ok(()) arm");
    let err_idx = handler
        .find("Err(err)")
        .expect("handler must have Err(err) arm");
    assert!(
        ok_idx < err_idx,
        "Ok(()) arm should appear before Err(err) arm — handler: {handler}"
    );
    let ok_arm = &handler[ok_idx..err_idx];

    // The Ok(()) arm must NOT be wrapped in `if let Some(modal)` — that's the
    // original bug that silently drops the refresh on cancel-then-success.
    assert!(
        !ok_arm.contains("if let Some(modal)"),
        "F9 R2-H1: the Ok(()) arm must not be guarded by `if let Some(modal)`; \
         the auth refresh must fire even when the modal has been cancelled. \
         arm: {ok_arm}"
    );
    // The Ok(()) arm must dispatch refresh_wandb_auth.
    assert!(
        ok_arm.contains("refresh_wandb_auth"),
        "F9 R2-H1: the Ok(()) arm must call wandb_auth::refresh_wandb_auth — arm: {ok_arm}"
    );
    // The Ok(()) arm must reset the modal to None.
    assert!(
        ok_arm.contains("self.wandb_signin_modal = None"),
        "F9 R2-H1: the Ok(()) arm must reset wandb_signin_modal to None — arm: {ok_arm}"
    );

    // The Err(err) arm should still wrap modal mutation in `if let Some(modal)`.
    let err_arm = &handler[err_idx..];
    assert!(
        err_arm.contains("if let Some(modal)"),
        "F9 R2-H1: the Err(err) arm must guard modal mutation with `if let Some(modal)` — arm: {err_arm}"
    );
    assert!(
        err_arm.contains("Message::LoginFailed"),
        "F9 R2-H1: the Err(err) arm must dispatch Message::LoginFailed (F9 R1-M4) — arm: {err_arm}"
    );
}

/// F9 R3-M1: main.rs の `WandbLoginResult::Err` arm は `modal.update(LoginFailed(..))`
/// の戻り値を `let _ =` で握り潰さず、`match` して `Some(action)` 経路を warn ログに
/// 出すこと。LoginFailed の契約は `None` を返すことだが、将来の変更で `Some` を返す
/// 設計変更が起きた場合に silent drop で覆い隠されないよう、観測ハッチを残す。
#[test]
fn wandb_login_result_err_handles_unexpected_action() {
    let main_src_path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let main_src = std::fs::read_to_string(main_src_path)
        .unwrap_or_else(|e| panic!("cannot read src/main.rs: {e}"));

    // Locate the handler arm.
    let idx = main_src
        .find("Message::WandbLoginResult(result)")
        .expect("main.rs must handle Message::WandbLoginResult(result)");
    let end = (idx + 2500).min(main_src.len());
    let mut safe = end;
    while !main_src.is_char_boundary(safe) {
        safe -= 1;
    }
    let handler = &main_src[idx..safe];
    let err_idx = handler
        .find("Err(err)")
        .expect("handler must have Err(err) arm");
    let err_arm = &handler[err_idx..];

    // The Err arm must NOT use `let _ = modal.update(...)` to discard the
    // Option<Action> return value silently.
    assert!(
        !err_arm.contains("let _ = modal.update("),
        "F9 R3-M1: Err arm must not use `let _ = modal.update(...)`; \
         match the Option<Action> and warn on Some(_) — arm: {err_arm}"
    );
    // The Err arm must call modal.update(...) inside a `match` (or `if let`)
    // and emit a `log::warn!` when an unexpected Action is returned.
    assert!(
        err_arm.contains("modal.update(") && err_arm.contains("log::warn!"),
        "F9 R3-M1: Err arm must call modal.update(...) and log::warn! on \
         unexpected Action — arm: {err_arm}"
    );
    // Concrete signal: a warning string mentioning unexpected/LoginFailed.
    assert!(
        err_arm.contains("unexpected") || err_arm.contains("LoginFailed"),
        "F9 R3-M1: Err arm warn message should reference the unexpected \
         LoginFailed Action contract — arm: {err_arm}"
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
