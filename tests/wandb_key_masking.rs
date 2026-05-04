//! F9c: key マスキング + Rust 側に判定ロジックが無いことの grep ガード

// ── mask_secrets() の基本動作（source-inspection + 直接実装テスト）──────────

/// mask_secrets のロジックをテスト用に再実装（bin-only crate のため）。
/// 本番コードと同じ正規表現を使う。
fn mask_secrets_test(line: &str) -> String {
    use regex::Regex;
    use std::sync::OnceLock;

    static KEY_PATTERN: OnceLock<Regex> = OnceLock::new();
    static HEX_PATTERN: OnceLock<Regex> = OnceLock::new();
    static BEARER_PATTERN: OnceLock<Regex> = OnceLock::new();

    let key_re = KEY_PATTERN
        .get_or_init(|| Regex::new(r"(?i)(wandb[_-]?api[_-]?key)\s*[=:]\s*\S+").unwrap());
    let hex_re = HEX_PATTERN.get_or_init(|| Regex::new(r"[0-9a-fA-F]{40,}").unwrap());
    let bearer_re = BEARER_PATTERN.get_or_init(|| Regex::new(r"(?i)(bearer)(\s+)\S+").unwrap());

    let masked = key_re.replace_all(line, "$1=***");
    let masked = hex_re.replace_all(&masked, "***");
    let masked = bearer_re.replace_all(&masked, "$1$2***");
    masked.into_owned()
}

#[test]
fn masks_wandb_api_key_env_format() {
    let key = "abc123def456abc123def456abc123def456abcd";
    let line = format!("WANDB_API_KEY={key}");
    let masked = mask_secrets_test(&line);
    assert!(
        masked.contains("***"),
        "WANDB_API_KEY= format must be masked, got: {masked}"
    );
    assert!(
        !masked.contains(key),
        "key value must not appear in output, got: {masked}"
    );
}

#[test]
fn masks_wandb_api_key_colon_format() {
    let key = "abc123def456abc123def456abc123def456abcd";
    let line = format!("WANDB_API_KEY: {key}");
    let masked = mask_secrets_test(&line);
    assert!(masked.contains("***"));
    assert!(!masked.contains(key));
}

#[test]
fn masks_40_char_hex_pattern() {
    let hex40 = "a".repeat(40);
    let line = format!("token={hex40}");
    let masked = mask_secrets_test(&line);
    assert!(
        !masked.contains(&hex40),
        "40-char hex must be masked, got: {masked}"
    );
    assert!(masked.contains("***"));
}

#[test]
fn masks_41_char_hex_pattern() {
    let hex41 = "b".repeat(41);
    let line = format!("key={hex41}");
    let masked = mask_secrets_test(&line);
    assert!(!masked.contains(&hex41));
    assert!(masked.contains("***"));
}

#[test]
fn does_not_mask_39_char_hex() {
    // 39桁は閾値未満なのでマスクしない
    let hex39 = "c".repeat(39);
    let line = format!("short={hex39}");
    let masked = mask_secrets_test(&line);
    // 39桁の hex はマスクされない
    assert!(
        masked.contains(&hex39),
        "39-char hex must NOT be masked, got: {masked}"
    );
}

#[test]
fn non_secret_line_unchanged() {
    let line = "normal log message without secrets 2026-05-04";
    let masked = mask_secrets_test(line);
    assert_eq!(masked, line, "non-secret line must be unchanged");
}

#[test]
fn case_insensitive_wandb_key_pattern() {
    let key = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2";
    let line = format!("wandb_api_key={key}");
    let masked = mask_secrets_test(&line);
    assert!(
        !masked.contains(key),
        "case-insensitive match must mask key"
    );
    assert!(masked.contains("***"));
}

/// C3 (R1 Phase 1): Bearer / bearer pattern を mask する。
/// 統一決定 44: subprocess stdout は WANDB_API_KEY / 40桁hex に加え
/// Authorization: Bearer 形式も全出口で mask されなければならない。
#[test]
fn test_mask_secrets_bearer_token_masked() {
    let masked = mask_secrets_test("Authorization: Bearer abc123");
    assert_eq!(
        masked, "Authorization: Bearer ***",
        "Bearer <token> must be masked, got: {masked}"
    );
}

#[test]
fn test_mask_secrets_bearer_lowercase_masked() {
    let masked = mask_secrets_test("authorization: bearer xyz789");
    assert_eq!(
        masked, "authorization: bearer ***",
        "lowercase bearer must also be masked, got: {masked}"
    );
}

// ── mask_secrets.rs ソース確認テスト ─────────────────────────────────────────

fn read_mask_secrets_src() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/mask_secrets.rs");
    std::fs::read_to_string(path).expect("failed to read src/mask_secrets.rs")
}

#[test]
fn mask_secrets_rs_defines_masked_line_newtype() {
    let src = read_mask_secrets_src();
    assert!(
        src.contains("pub struct MaskedLine"),
        "mask_secrets.rs must define MaskedLine newtype"
    );
    assert!(
        src.contains("pub fn mask_secrets"),
        "mask_secrets.rs must define pub fn mask_secrets"
    );
    assert!(
        src.contains("MaskedLine"),
        "mask_secrets must return MaskedLine"
    );
}

#[test]
fn mask_secrets_rs_uses_40_char_hex_pattern() {
    let src = read_mask_secrets_src();
    assert!(
        src.contains("{40,}"),
        "mask_secrets.rs must use 40-char minimum hex pattern"
    );
}

// ── Rust 側に認証ロジックが混入していない grep ガード ─────────────────────────

fn all_src_rs_contents() -> Vec<(std::path::PathBuf, String)> {
    let src_dir = std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/src"));
    let mut results = Vec::new();
    for entry in walkdir::WalkDir::new(src_dir)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if entry.path().extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let content = match std::fs::read_to_string(entry.path()) {
            Ok(c) => c,
            Err(_) => continue,
        };
        results.push((entry.path().to_path_buf(), content));
    }
    results
}

#[test]
fn no_wandb_api_key_literal_in_src() {
    for (path, content) in all_src_rs_contents() {
        // mask_secrets.rs は正規表現の一部として "wandb" を含むが
        // "WANDB_API_KEY" の文字列リテラルそのものは含まない
        // wandb_auth.rs, mask_secrets.rs はパターン文字列の一部として許容
        let filename = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if filename == "mask_secrets.rs" {
            // マスキングコードはパターン文字列として wandb_api_key を含む — OK
            continue;
        }
        assert!(
            !content.contains("\"WANDB_API_KEY\""),
            "{} contains \"WANDB_API_KEY\" string literal — must stay in Python only (P9 Q11)",
            path.display()
        );
    }
}

#[test]
fn no_api_wandb_ai_in_src() {
    for (path, content) in all_src_rs_contents() {
        assert!(
            !content.contains("api.wandb.ai"),
            "{} contains \"api.wandb.ai\" — netrc machine name must stay in Python only (P9 Q11)",
            path.display()
        );
    }
}

#[test]
fn no_netrc_literal_in_src() {
    for (path, content) in all_src_rs_contents() {
        assert!(
            !content.contains(".netrc") && !content.contains("_netrc"),
            "{} contains \".netrc\" or \"_netrc\" — netrc access must stay in Python only (P9 Q11)",
            path.display()
        );
    }
}

#[test]
fn no_key_field_in_flowsurface_state() {
    for (path, content) in all_src_rs_contents() {
        assert!(
            !content.contains("wandb_api_key:"),
            "{} contains \"wandb_api_key:\" field definition — API key must NOT be stored in Rust state",
            path.display()
        );
    }
}

// ── panic hook がソースに登録されていることを確認 ─────────────────────────────

#[test]
fn panic_hook_registered_in_main() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("failed to read src/main.rs");

    // fn main() の先頭付近に set_hook があることを確認
    let main_start = src.find("fn main()").expect("fn main() must exist");
    let main_body = &src[main_start..main_start + 1000];

    assert!(
        main_body.contains("set_hook"),
        "fn main() must register a panic hook with std::panic::set_hook near its start"
    );
    assert!(
        main_body.contains("mask_secrets"),
        "panic hook in main() must call mask_secrets to prevent key leakage"
    );
}

// ── property-based テスト: 任意 40+ 桁 hex を含む文字列が必ずマスクされる ─────

use proptest::prelude::*;

proptest! {
    #[test]
    fn any_40_char_hex_always_masked(
        // 40〜60 桁の hex 文字列
        hex in "[0-9a-fA-F]{40,60}"
    ) {
        let line = format!("prefix_{hex}_suffix");
        let masked = mask_secrets_test(&line);
        prop_assert!(
            !masked.contains(&hex),
            "40+ char hex must always be masked, got: {masked}"
        );
        prop_assert!(masked.contains("***"));
    }

    #[test]
    fn wandb_key_env_format_always_masked(
        // 任意の 40〜64 桁 hex を key 値として
        key in "[0-9a-fA-F]{40,64}"
    ) {
        let line = format!("WANDB_API_KEY={key}");
        let masked = mask_secrets_test(&line);
        prop_assert!(
            !masked.contains(&key),
            "WANDB_API_KEY=<hex> must always be masked, got: {masked}"
        );
    }
}
