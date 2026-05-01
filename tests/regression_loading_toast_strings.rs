//! 廃止文字列「取得中」が `Toast::info` 生成箇所に残らないことをガードする
//! リグレッションテスト。新バッジ文言「更新中…」とは衝突しない。
//!
//! [R04] 注記: この regex は `Toast::info("...取得中...")` のような静的リテラル
//! 前提で書かれている。将来 `Toast::info(format!("...{x}...取得中..."))` のように
//! `format!` 経由で動的生成された場合、`[^)]*` が format! 内側の `(` で
//! マッチを切り、検知漏れする可能性がある。動的生成版を導入する際は、
//! 別途「format! 内 引数文字列の取得中検知」を AST ベースで追加すること。

use std::fs;
use std::path::Path;

fn read_rs_files_recursive(dir: &Path, out: &mut Vec<String>) {
    for entry in fs::read_dir(dir).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();
        if path.is_dir() {
            read_rs_files_recursive(&path, out);
        } else if path.extension().and_then(|s| s.to_str()) == Some("rs") {
            if let Ok(content) = fs::read_to_string(&path) {
                out.push(format!("{}\n{}", path.display(), content));
            }
        }
    }
}

#[test]
fn no_toast_info_with_torichu() {
    let mut files = Vec::new();
    read_rs_files_recursive(Path::new("src"), &mut files);
    let re = regex::Regex::new(r"Toast::info\([^)]*取得中").unwrap();
    for blob in &files {
        assert!(
            !re.is_match(blob),
            "廃止文字列『取得中』を含む Toast::info が残っています:\n{}",
            blob.lines().next().unwrap_or("")
        );
    }
}
