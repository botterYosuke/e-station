//! W&B サインイン / サインアウト確認モーダル。
//!
//! # セキュリティ上の注意
//! - API キーはビュー内でマスク表示（`text_input::secure`）する。
//! - キーをログ・トレースに出力しない。
//! - `Action::Login` で呼び出し側に渡した後、入力バッファを即座にクリアする。
//! - subprocess 起動時は argv にキーを渡さず stdin pipe 経由で渡す（呼び出し側責任）。

// F9 R1-H8: removed module-wide `#![allow(dead_code)]`; per-item allows are
// applied to public items still pending wiring in main.rs.

use iced::{
    Element,
    widget::{button, column, container, row, text, text_input},
};

// ─── WandbSignInModal ────────────────────────────────────────────────────────

/// W&B API キーを入力して `wandb login --relogin` を実行するモーダル。
#[derive(Debug, Default)]
pub struct WandbSignInModal {
    /// API キー入力バッファ（表示はマスク）。
    pub api_key_input: String,
    /// subprocess 起動中フラグ。
    pub submitting: bool,
    /// エラーメッセージ（Some のとき赤文字で表示）。
    pub error: Option<String>,
}

/// モーダル内部イベント。
#[derive(Debug, Clone)]
pub enum Message {
    /// API キー入力フィールドの変更。
    ApiKeyChanged(String),
    /// ブラウザで API キー取得ページを開く。
    OpenBrowserForKey,
    /// ログイン実行。
    Login,
    /// モーダルを閉じる。
    Cancel,
    /// F9 R1-M4: subprocess `wandb login` 失敗。親が直接フィールドを書き換える
    /// のではなく Message 経由で渡し、内部状態 (`submitting`/`error`) は
    /// `update()` 内で一元管理する。
    LoginFailed(String),
}

/// 呼び出し側（main.rs）に返す意図表明。
pub enum Action {
    /// ブラウザで `https://wandb.ai/authorize` を開く。
    OpenBrowserForKey,
    /// `wandb login --relogin` を **stdin pipe** 経由でキーを渡して実行する。
    /// argv にキーを渡してはならない（プロセスリスト・シェル履歴への漏洩防止）。
    Login { api_key: String },
    /// モーダルを閉じる。
    Cancel,
}

impl WandbSignInModal {
    pub fn new() -> Self {
        Self::default()
    }

    /// メッセージを処理し、必要なら `Action` を返す。
    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::ApiKeyChanged(v) => {
                self.api_key_input = v;
                None
            }
            Message::OpenBrowserForKey => Some(Action::OpenBrowserForKey),
            Message::Login => {
                if self.api_key_input.is_empty() {
                    return None;
                }
                let api_key = std::mem::take(&mut self.api_key_input);
                self.submitting = true;
                Some(Action::Login { api_key })
            }
            Message::Cancel => {
                self.api_key_input.clear();
                Some(Action::Cancel)
            }
            Message::LoginFailed(err) => {
                // F9 R1-M4: 失敗を Message 経由で受け取り、内部状態を update()
                // 内に閉じ込める。submitting=false でボタン押下を再有効化し、
                // error にメッセージを格納して view が赤文字で表示する。
                self.submitting = false;
                self.error = Some(err);
                None
            }
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let key_input = text_input("API キーをここに入力", &self.api_key_input)
            .secure(true)
            .on_input(Message::ApiKeyChanged);

        let browser_btn =
            button(text("ブラウザで API キーを取得")).on_press(Message::OpenBrowserForKey);

        let login_btn = {
            let b = button(text("ログイン"));
            if self.api_key_input.is_empty() || self.submitting {
                b
            } else {
                b.on_press(Message::Login)
            }
        };

        let cancel_btn = button(text("キャンセル")).on_press(Message::Cancel);

        let error_row: Element<'_, Message> = if let Some(err) = &self.error {
            text(err.as_str()).color([0.9_f32, 0.2, 0.2]).into()
        } else {
            text("").into()
        };

        container(
            column![
                text("W&B API キーでサインイン").size(16),
                key_input,
                browser_btn,
                error_row,
                row![cancel_btn, login_btn].spacing(8),
            ]
            .spacing(8),
        )
        .into()
    }

    /// ログインボタンが有効かどうか（空入力・送信中は無効）。
    /// view() 側のロジック（同等条件）と乖離しないよう unit test 用に保持。
    #[allow(dead_code)]
    pub fn can_submit(&self) -> bool {
        !self.api_key_input.is_empty() && !self.submitting
    }
}

// ─── WandbSignOutConfirm ─────────────────────────────────────────────────────
//
// F9 R1-H8: this confirm dialog is currently invoked through the shared
// `confirm_dialog` overlay (see `Message::WandbLogoutConfirmed` in main.rs)
// rather than via this stand-alone modal. The struct + enums are kept for
// future restructuring and are exercised by unit tests below; per-item
// `#[allow(dead_code)]` annotations replace the previous module-wide allow.

/// W&B サインアウト確認ダイアログ。
#[allow(dead_code)]
#[derive(Debug, Default)]
pub struct WandbSignOutConfirm;

/// サインアウト確認ダイアログのメッセージ。
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub enum SignOutMessage {
    ConfirmSignOut,
    CancelSignOut,
}

/// サインアウト確認ダイアログのアクション。
#[allow(dead_code)]
pub enum SignOutAction {
    SignOut,
    Cancel,
}

#[allow(dead_code)]
impl WandbSignOutConfirm {
    pub fn new() -> Self {
        Self
    }

    pub fn update(&self, message: SignOutMessage) -> SignOutAction {
        match message {
            SignOutMessage::ConfirmSignOut => SignOutAction::SignOut,
            SignOutMessage::CancelSignOut => SignOutAction::Cancel,
        }
    }

    pub fn view(&self) -> Element<'_, SignOutMessage> {
        container(
            column![
                text("本当にログアウトしますか？").size(16),
                row![
                    button(text("いいえ")).on_press(SignOutMessage::CancelSignOut),
                    button(text("はい")).on_press(SignOutMessage::ConfirmSignOut),
                ]
                .spacing(8),
            ]
            .spacing(8),
        )
        .into()
    }
}

// ─── Unit tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancel_clears_api_key_input() {
        let mut modal = WandbSignInModal::new();
        modal.api_key_input = "test-key".into();
        let action = modal.update(Message::Cancel);
        assert!(matches!(action, Some(Action::Cancel)));
        assert!(
            modal.api_key_input.is_empty(),
            "Cancel should clear api_key_input"
        );
    }

    #[test]
    fn login_takes_key_and_clears_input() {
        let mut modal = WandbSignInModal::new();
        modal.api_key_input = "mykey123".into();
        let action = modal.update(Message::Login);
        assert!(
            modal.api_key_input.is_empty(),
            "Login should clear api_key_input"
        );
        assert!(matches!(action, Some(Action::Login { api_key }) if api_key == "mykey123"));
    }

    #[test]
    fn login_on_empty_input_returns_none() {
        let mut modal = WandbSignInModal::new();
        let action = modal.update(Message::Login);
        assert!(action.is_none());
    }

    #[test]
    fn open_browser_action_returned() {
        let mut modal = WandbSignInModal::new();
        let action = modal.update(Message::OpenBrowserForKey);
        assert!(matches!(action, Some(Action::OpenBrowserForKey)));
    }

    #[test]
    fn signout_confirm_yields_signout() {
        let confirm = WandbSignOutConfirm::new();
        let action = confirm.update(SignOutMessage::ConfirmSignOut);
        assert!(matches!(action, SignOutAction::SignOut));
    }

    #[test]
    fn signout_cancel_yields_cancel() {
        let confirm = WandbSignOutConfirm::new();
        let action = confirm.update(SignOutMessage::CancelSignOut);
        assert!(matches!(action, SignOutAction::Cancel));
    }
}
