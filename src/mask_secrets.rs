//! API key マスキング — subprocess stdout reader → UI/log/tracing の全出口で使う。

use regex::Regex;
use std::sync::OnceLock;

/// subprocess stdout の 1 行。mask_secrets() を通してのみ生成できる。
/// raw String を UI / ログに直接渡すコードはコンパイルエラーになる（newtype 強制）。
#[derive(Debug, Clone)]
pub struct MaskedLine(String);

impl MaskedLine {
    /// inner String を取得する（表示・ログ用途のみ）。
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// `line` から W&B API key パターンを検出して `***` に置換し `MaskedLine` を返す。
///
/// 検出パターン:
/// 1. `WANDB_API_KEY=<value>` / `WANDB_API_KEY: <value>` 形式
/// 2. 40 桁以上の連続 hex 文字列（API key の実体）
pub fn mask_secrets(line: &str) -> MaskedLine {
    static KEY_PATTERN: OnceLock<Regex> = OnceLock::new();
    static HEX_PATTERN: OnceLock<Regex> = OnceLock::new();

    let key_re = KEY_PATTERN
        .get_or_init(|| Regex::new(r"(?i)(wandb[_-]?api[_-]?key)\s*[=:]\s*\S+").unwrap());
    let hex_re = HEX_PATTERN.get_or_init(|| Regex::new(r"[0-9a-fA-F]{40,}").unwrap());

    let masked = key_re.replace_all(line, "$1=***");
    let masked = hex_re.replace_all(&masked, "***");
    MaskedLine(masked.into_owned())
}
