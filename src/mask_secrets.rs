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
    // F9 R2-M6: `into_string()` was removed. It had zero callers and adding it
    // back would defeat the newtype's purpose (the inner String is only meant
    // to flow into displays/logs via `as_str` / `Display` / `AsRef<str>`).
    // The regression test `mask_secrets_into_string_does_not_exist` guards
    // against accidental re-introduction.
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
//
// F9 R1-M6: 正規表現リテラルを定数として独立させる（単一情報源）。
// `tests/wandb_key_masking.rs::mask_secrets_test` は bin-only crate のため
// この定数を `use` できない（include_str! / source-inspection で検証する）。
// **このパターンを変更する際は `tests/wandb_key_masking.rs` の `mask_secrets_test`
// 内ハードコードも同時に更新すること**。両者の対称性は本番テスト
// `mask_secrets_rs_uses_40_char_hex_pattern` で部分的に保護されている。
const KEY_REGEX: &str = r"(?i)(wandb[_-]?api[_-]?key)\s*[=:]\s*\S+";
const HEX_REGEX: &str = r"[0-9a-fA-F]{40,}";
const BEARER_REGEX: &str = r"(?i)(bearer)(\s+)\S+";

static KEY_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(KEY_REGEX).expect("WANDB_API_KEY mask pattern is a valid regex literal")
});
static HEX_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(HEX_REGEX).expect("hex mask pattern is a valid regex literal"));
static BEARER_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(BEARER_REGEX).expect("bearer mask pattern is a valid regex literal")
});

pub fn mask_secrets(line: &str) -> MaskedLine {
    let masked = KEY_PATTERN.replace_all(line, "$1=***");
    let masked = HEX_PATTERN.replace_all(&masked, "***");
    let masked = BEARER_PATTERN.replace_all(&masked, "$1$2***");
    MaskedLine(masked.into_owned())
}
