//! Structural pins for invariant **T35-LoginUpdate**.
//!
//! `Message::RequestTachibanaLogin` の `update()` ハンドラが保持すべき 4 つの
//! 不変条件をソースコードレベルで固定する。
//!
//! `Flowsurface` は `window::open()` / `LayoutManager` 等の GUI 依存が深く
//! フル構造体インスタンス化が困難なため、既存ピン群（`venue_ready_bridge_*`
//! / `venue_names_includes_tachibana` 等）と同じアプローチを採用する。
//!
//! # ピン一覧
//!
//! | テスト名 | ピンする不変条件 |
//! |---|---|
//! | `request_login_calls_try_claim` | ハンドラが `try_claim_login_in_flight()` を呼ぶ |
//! | `request_login_sends_request_venue_login_command` | `Command::RequestVenueLogin` を IPC 送信する |
//! | `request_login_hooks_tachibana_login_ipc_result` | callback が `TachibanaLoginIpcResult` である |
//! | `request_login_returns_none_when_no_connection` | 未接続時に `Task::none()` で早期リターンする |

fn read_handler_body() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");

    // `Message::RequestTachibanaLogin` アームの開始位置を見つける。
    let needle = "Message::RequestTachibanaLogin(trigger)";
    let start = src
        .find(needle)
        .expect("Message::RequestTachibanaLogin(trigger) not found in src/main.rs");

    // 次の `Message::` アームの開始位置を末端とする（最大 3000 バイト）。
    // これにより他のハンドラの記述が混入しない。
    let after = &src[start..];
    let end = after
        .find("\n            Message::")
        .map(|n| n.min(3000))
        .unwrap_or(3000.min(after.len()));

    after[..end].to_string()
}

/// `RequestTachibanaLogin` ハンドラが `try_claim_login_in_flight()` を呼ぶことを確認。
///
/// これがなければ二重押し抑制が機能せず、高速な二連打で IPC が二重送信される
/// （Reviewer 2026-04-26 R4 MEDIUM-2）。
#[test]
fn request_login_calls_try_claim() {
    let body = read_handler_body();
    assert!(
        body.contains("try_claim_login_in_flight()"),
        "Message::RequestTachibanaLogin handler must call \
         `try_claim_login_in_flight()` to suppress duplicate IPC sends. \
         Without it, rapid double-press dispatches two RequestVenueLogin \
         commands — a tkinter helper spawns twice. \
         T35-LoginUpdate / R4 MEDIUM-2."
    );
}

/// `RequestTachibanaLogin` ハンドラが `Command::RequestVenueLogin` を IPC 送信することを確認。
///
/// IPC 送信コマンド名が変更・削除された場合に無音の regression となるのを防ぐ。
#[test]
fn request_login_sends_request_venue_login_command() {
    let body = read_handler_body();
    assert!(
        body.contains("Command::RequestVenueLogin"),
        "Message::RequestTachibanaLogin handler must send \
         `Command::RequestVenueLogin` over the IPC connection. \
         Renaming or removing this command breaks the Python engine \
         dispatch path (`handle_request_venue_login`). \
         T35-LoginUpdate."
    );
}

/// `Task::perform` の callback が `Message::TachibanaLoginIpcResult` であることを確認。
///
/// callback が変わると IPC 送信失敗時のロールバック（`LoginInFlight` → `Idle`）が
/// 発火しなくなり、ユーザーが再試行不能になる（Reviewer 2026-04-26 R4 MEDIUM-2）。
#[test]
fn request_login_hooks_tachibana_login_ipc_result() {
    let body = read_handler_body();
    assert!(
        body.contains("Message::TachibanaLoginIpcResult"),
        "Message::RequestTachibanaLogin handler must use \
         `Message::TachibanaLoginIpcResult` as the Task::perform callback. \
         Without this hook, IPC send failures do not roll back the FSM \
         from LoginInFlight to Idle, leaving the user unable to retry. \
         T35-LoginUpdate / R4 MEDIUM-2."
    );
}

/// 未接続時（`engine_connection` が None）に `Task::none()` で早期リターンすることを確認。
///
/// 接続が確立していない状態で IPC 送信を試みると panic または silent failure になる。
/// ガード節がないと手動ボタン押下でもクラッシュしうる。
#[test]
fn request_login_returns_none_when_no_connection() {
    let body = read_handler_body();

    // ガード節のパターン: `engine_connection` 参照 + `Task::none()` の両方が存在する
    assert!(
        body.contains("engine_connection"),
        "Message::RequestTachibanaLogin handler must guard against \
         a missing engine connection (check `engine_connection`). \
         Without the guard, a button press before engine startup \
         attempts an IPC send on a None connection. \
         T35-LoginUpdate."
    );
    assert!(
        body.contains("Task::none()"),
        "Message::RequestTachibanaLogin handler must return `Task::none()` \
         when the engine connection is unavailable. \
         T35-LoginUpdate."
    );
}
