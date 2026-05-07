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

/// `line` から secrets パターンを検出して `***` に置換し `MaskedLine` を返す。
///
/// 検出パターン:
/// 1. 環境変数スタイルの key=value / key: value（*_KEY / *_SECRET / *_TOKEN / *_PASSWORD）
/// 2. 40 桁以上の連続 hex 文字列（API key の実体）
/// 3. `Bearer <token>` / `bearer <token>` (Authorization ヘッダ等)
// R2-M4: OnceLock + unwrap() を LazyLock + expect() に置換。初期化は
// プロセス起動時に1度だけ行われ、unwrap が走らないことが構造的に保証される。
// expect メッセージで panic 文言を明示し、テスト時の診断を容易化する。
const ENV_KEY_REGEX: &str =
    r"(?i)([a-z_]*(?:api[_-]?key|secret|token|password)[a-z_0-9]*)\s*[=:]\s*\S+";
const HEX_REGEX: &str = r"[0-9a-fA-F]{40,}";
const BEARER_REGEX: &str = r"(?i)(bearer)(\s+)\S+";

static ENV_KEY_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(ENV_KEY_REGEX).expect("env key mask pattern is a valid regex literal")
});
static HEX_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(HEX_REGEX).expect("hex mask pattern is a valid regex literal"));
static BEARER_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(BEARER_REGEX).expect("bearer mask pattern is a valid regex literal")
});

pub fn mask_secrets(line: &str) -> MaskedLine {
    let masked = ENV_KEY_PATTERN.replace_all(line, "$1=***");
    let masked = HEX_PATTERN.replace_all(&masked, "***");
    let masked = BEARER_PATTERN.replace_all(&masked, "$1$2***");
    MaskedLine(masked.into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_generic_api_key_assignment() {
        let line = "SOME_API_KEY=abc123";
        assert_eq!(mask_secrets(line).as_str(), "SOME_API_KEY=***");
    }

    #[test]
    fn masks_generic_api_key_colon() {
        let line = "some_api_key: abc123";
        assert_eq!(mask_secrets(line).as_str(), "some_api_key=***");
    }

    #[test]
    fn masks_secret_token_password() {
        assert!(mask_secrets("MY_SECRET=xyz").as_str().contains("***"));
        assert!(
            mask_secrets("db_password: hunter2")
                .as_str()
                .contains("***")
        );
        assert!(
            mask_secrets("ACCESS_TOKEN=tok_abc")
                .as_str()
                .contains("***")
        );
    }

    #[test]
    fn masks_long_hex() {
        let line = "key=aaaa0000bbbb1111cccc2222dddd3333eeee4444";
        assert!(mask_secrets(line).as_str().contains("***"));
    }

    #[test]
    fn masks_bearer_token() {
        let line = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9";
        assert!(mask_secrets(line).as_str().contains("Bearer ***"));
    }

    #[test]
    fn does_not_mask_plain_text() {
        let line = "replay finished successfully";
        assert_eq!(mask_secrets(line).as_str(), line);
    }
}
