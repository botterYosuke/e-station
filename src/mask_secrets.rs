//! API key マスキング — subprocess stdout reader → UI/log/tracing の全出口で使う。

use regex::Regex;
use std::sync::LazyLock;

/// subprocess stdout の 1 行。mask_secrets() を通してのみ生成できる。
/// raw String を UI / ログに直接渡すコードはコンパイルエラーになる（newtype 強制）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MaskedLine(String);

impl MaskedLine {
    /// inner String を取得する（表示・ログ用途のみ）。
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// inner String を消費して取り出す（必要時のみ）。
    #[allow(dead_code)]
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for MaskedLine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl AsRef<str> for MaskedLine {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

/// `line` から W&B API key パターンを検出して `***` に置換し `MaskedLine` を返す。
///
/// 検出パターン:
/// 1. `WANDB_API_KEY=<value>` / `WANDB_API_KEY: <value>` 形式
/// 2. 40 桁以上の連続 hex 文字列（API key の実体）
/// 3. `Bearer <token>` / `bearer <token>` (Authorization ヘッダ等)
// R2-M4: OnceLock + unwrap() を LazyLock + expect() に置換。初期化は
// プロセス起動時に1度だけ行われ、unwrap が走らないことが構造的に保証される。
// expect メッセージで panic 文言を明示し、テスト時の診断を容易化する。
static KEY_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(wandb[_-]?api[_-]?key)\s*[=:]\s*\S+")
        .expect("WANDB_API_KEY mask pattern is a valid regex literal")
});
static HEX_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[0-9a-fA-F]{40,}").expect("hex mask pattern is a valid regex literal")
});
static BEARER_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(bearer)(\s+)\S+").expect("bearer mask pattern is a valid regex literal")
});

pub fn mask_secrets(line: &str) -> MaskedLine {
    let masked = KEY_PATTERN.replace_all(line, "$1=***");
    let masked = HEX_PATTERN.replace_all(&masked, "***");
    let masked = BEARER_PATTERN.replace_all(&masked, "$1$2***");
    MaskedLine(masked.into_owned())
}
