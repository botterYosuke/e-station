//! W&B 認証状態 — Python subprocess の stdout JSON を Rust 側で保持するだけ。
//! 認証判定ロジック（netrc・env 解決）は Python 側に完全委譲（P9 Q11）。
#![allow(dead_code)]

use serde::Deserialize;

/// Python `examples/wandb/check_auth.py` が stdout に返す JSON の構造体。
/// Rust 側は判定ロジックを持たず、この struct を受け取ってメニュー状態に流すだけ。
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WandbAuthState {
    pub authenticated: bool,
    /// "env" | "netrc" | "none"
    pub method: String,
    pub username: Option<String>,
    pub error: Option<String>,
}

impl WandbAuthState {
    /// fail-closed のデフォルト（未認証）。アプリ起動直後や subprocess 失敗時に使う。
    pub fn unauthenticated() -> Self {
        Self {
            authenticated: false,
            method: "none".to_string(),
            username: None,
            error: None,
        }
    }
}

/// ローカルの run-buffer インデックス — メニュー有効化の判定に使う。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunBufferIndex {
    /// `status="completed"` の最新 run id（あれば）
    pub latest_completed: Option<String>,
    /// run-buffer/ 配下の全ディレクトリ数
    pub total: usize,
}

impl RunBufferIndex {
    pub fn empty() -> Self {
        Self {
            latest_completed: None,
            total: 0,
        }
    }

    /// run-buffer/ ディレクトリをスキャンして最新の completed run を検索する。
    /// ファイルシステムエラー時は empty() を返す（fail-closed）。
    pub fn scan(run_buffer_dir: &std::path::Path) -> Self {
        if !run_buffer_dir.exists() {
            return Self::empty();
        }

        let entries = match std::fs::read_dir(run_buffer_dir) {
            Ok(e) => e,
            Err(err) => {
                log::warn!("RunBufferIndex::scan: failed to read dir: {err}");
                return Self::empty();
            }
        };

        let mut total = 0usize;
        let mut completed: Vec<(String, String)> = Vec::new(); // (started_at, run_id)

        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            total += 1;

            let meta_path = path.join("meta.json");
            let content = match std::fs::read_to_string(&meta_path) {
                Ok(c) => c,
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan: failed to read {}: {err}",
                        meta_path.display()
                    );
                    continue;
                }
            };

            let meta: serde_json::Value = match serde_json::from_str(&content) {
                Ok(v) => v,
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan: failed to parse {}: {err}",
                        meta_path.display()
                    );
                    continue;
                }
            };

            let status = meta.get("status").and_then(|v| v.as_str()).unwrap_or("");
            if status != "completed" {
                continue;
            }

            let run_id = match meta.get("run_id").and_then(|v| v.as_str()) {
                Some(id) => id.to_string(),
                None => continue,
            };
            let started_at = meta
                .get("started_at")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            completed.push((started_at, run_id));
        }

        // started_at の降順ソートで最新を先頭に
        completed.sort_by(|a, b| b.0.cmp(&a.0));

        let latest_completed = completed.into_iter().next().map(|(_, run_id)| run_id);

        Self {
            latest_completed,
            total,
        }
    }
}
