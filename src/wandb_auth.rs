//! W&B 認証状態 — Python subprocess の stdout JSON を Rust 側で保持するだけ。
//! 認証判定ロジック（netrc・env 解決）は Python 側に完全委譲（P9 Q11）。
#![allow(dead_code)]

use serde::Deserialize;

/// `examples/wandb/check_auth.py` を subprocess で起動し、stdout JSON を
/// `WandbAuthState` にデシリアライズして返す。
///
/// 起動コマンド: `uv run --with wandb python examples/wandb/check_auth.py`
/// 失敗時（spawn 失敗・JSON 不正・timeout）は `WandbAuthState::unauthenticated()` を返す（fail-closed）。
pub async fn refresh_wandb_auth() -> WandbAuthState {
    use tokio::process::Command;

    let output = Command::new("uv")
        .args([
            "run",
            "--with",
            "wandb",
            "python",
            "examples/wandb/check_auth.py",
        ])
        .output()
        .await;

    let output = match output {
        Ok(o) => o,
        Err(err) => {
            log::warn!("refresh_wandb_auth: failed to spawn check_auth.py: {err}");
            return WandbAuthState::unauthenticated();
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    // stdout の最初の非空行を JSON としてパース
    let line = stdout.lines().find(|l| !l.trim().is_empty()).unwrap_or("");

    match serde_json::from_str::<WandbAuthState>(line) {
        Ok(state) => state,
        Err(err) => {
            log::warn!(
                "refresh_wandb_auth: failed to parse JSON from check_auth.py: {err} (line={line:?})"
            );
            WandbAuthState::unauthenticated()
        }
    }
}

/// 認証メソッド — `check_auth.py` が返す `"env" | "netrc" | "none"` を type-safe な
/// enum で受け取る（M4, R1 Phase 1）。未知の値は deserialize で reject される。
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuthMethod {
    Env,
    Netrc,
    None,
}

/// Python `examples/wandb/check_auth.py` が stdout に返す JSON の構造体。
/// Rust 側は判定ロジックを持たず、この struct を受け取ってメニュー状態に流すだけ。
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WandbAuthState {
    pub authenticated: bool,
    pub method: AuthMethod,
    pub username: Option<String>,
    pub error: Option<String>,
}

impl WandbAuthState {
    /// fail-closed のデフォルト（未認証）。アプリ起動直後や subprocess 失敗時に使う。
    pub fn unauthenticated() -> Self {
        Self {
            authenticated: false,
            method: AuthMethod::None,
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

/// One row in the W&B 送信履歴 modal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunBufferEntry {
    pub run_id: String,
    pub status: String,
    pub started_at: String,
    pub strategy_file: Option<String>,
}

/// Async scan of every `<run-buffer>/<run-id>/meta.json` returning a flat
/// list sorted by `started_at` (newest first). Used by the 送信履歴 modal
/// to display past runs (completed / aborted / running). FS errors and
/// individual unreadable meta.json files are logged and skipped.
pub async fn list_run_buffer_entries(run_buffer_dir: &std::path::Path) -> Vec<RunBufferEntry> {
    if tokio::fs::metadata(run_buffer_dir).await.is_err() {
        return Vec::new();
    }
    let mut entries = match tokio::fs::read_dir(run_buffer_dir).await {
        Ok(e) => e,
        Err(err) => {
            log::warn!("list_run_buffer_entries: read_dir failed: {err}");
            return Vec::new();
        }
    };

    let mut rows: Vec<RunBufferEntry> = Vec::new();
    loop {
        let entry = match entries.next_entry().await {
            Ok(Some(e)) => e,
            Ok(None) => break,
            Err(err) => {
                log::warn!("list_run_buffer_entries: next_entry failed: {err}");
                break;
            }
        };
        let path = entry.path();
        let is_dir = entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false);
        if !is_dir {
            continue;
        }
        let meta_path = path.join("meta.json");
        let content = match tokio::fs::read_to_string(&meta_path).await {
            Ok(c) => c,
            Err(err) => {
                log::warn!(
                    "list_run_buffer_entries: read {} failed: {err}",
                    meta_path.display()
                );
                continue;
            }
        };
        let dir_name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let entry = match parse_meta_loose(&content, &dir_name) {
            Some(e) => e,
            None => {
                log::warn!(
                    "list_run_buffer_entries: parse {} failed",
                    meta_path.display()
                );
                continue;
            }
        };
        rows.push(entry);
    }
    rows.sort_by(|a, b| b.started_at.cmp(&a.started_at));
    rows
}

/// 履歴モーダル専用の緩い meta.json パーサー。`run_id` / `status` が欠けた
/// 古い・部分破損・手修正済みの run-buffer も「壊れ気味でも表示する」方針で
/// `RunBufferEntry` に変換する（F9 リグレッション対策）。
///
/// - `run_id` 欠落 → ディレクトリ名にフォールバック
/// - `status` 欠落 → `"unknown"`
/// - JSON 自体がパース不能なときのみ `None`
fn parse_meta_loose(content: &str, dir_name: &str) -> Option<RunBufferEntry> {
    #[derive(Deserialize)]
    struct MetaLoose {
        #[serde(default)]
        run_id: Option<String>,
        #[serde(default)]
        status: Option<String>,
        #[serde(default)]
        started_at: Option<String>,
        #[serde(default)]
        strategy_file: Option<String>,
    }
    let m: MetaLoose = serde_json::from_str(content).ok()?;
    Some(RunBufferEntry {
        run_id: m.run_id.unwrap_or_else(|| dir_name.to_string()),
        status: m.status.unwrap_or_else(|| "unknown".to_string()),
        started_at: m.started_at.unwrap_or_default(),
        strategy_file: m.strategy_file,
    })
}

/// `<run-buffer>/<run-id>/meta.json` の型付き表現（M13, R1 Phase 3-C）。
///
/// `serde_json::Value` での動的パースから型付き struct に切り替えることで、
/// フィールド名変更が compile-time に検出されるようにする。
/// `#[serde(deny_unknown_fields)]` は付けない（forward compat のため未知フィールドは
/// 黙って無視する）。
#[derive(Debug, Deserialize)]
struct MetaJson {
    run_id: String,
    status: String,
    #[serde(default)]
    started_at: Option<String>,
    /// 履歴 UI 用。古い meta.json では存在しないため Option。
    #[serde(default)]
    strategy_file: Option<String>,
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
    ///
    /// 同期版は起動時の一度きり初期化用。GUI update loop からは
    /// [`scan_async`](Self::scan_async) を `Task::perform` 経由で呼ぶこと（M3）。
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

            let meta: MetaJson = match serde_json::from_str(&content) {
                Ok(v) => v,
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan: failed to parse {}: {err}",
                        meta_path.display()
                    );
                    continue;
                }
            };

            if meta.status != "completed" {
                continue;
            }

            let started_at = meta.started_at.unwrap_or_default();
            completed.push((started_at, meta.run_id));
        }

        // started_at の降順ソートで最新を先頭に
        completed.sort_by(|a, b| b.0.cmp(&a.0));

        let latest_completed = completed.into_iter().next().map(|(_, run_id)| run_id);

        Self {
            latest_completed,
            total,
        }
    }

    /// `scan` の async 版（M3, R1 Phase 3-C）。
    ///
    /// `tokio::fs` ベースで I/O を行い、iced の update loop をブロックしない。
    /// パース・ソートは CPU 負荷が小さいため inline で行う。
    /// ファイルシステムエラー時は `empty()` を返す（fail-closed）。
    pub async fn scan_async(run_buffer_dir: &std::path::Path) -> Self {
        if tokio::fs::metadata(run_buffer_dir).await.is_err() {
            return Self::empty();
        }

        let mut entries = match tokio::fs::read_dir(run_buffer_dir).await {
            Ok(e) => e,
            Err(err) => {
                log::warn!("RunBufferIndex::scan_async: failed to read dir: {err}");
                return Self::empty();
            }
        };

        let mut total = 0usize;
        let mut completed: Vec<(String, String)> = Vec::new(); // (started_at, run_id)

        loop {
            let entry = match entries.next_entry().await {
                Ok(Some(e)) => e,
                Ok(None) => break,
                Err(err) => {
                    log::warn!("RunBufferIndex::scan_async: next_entry failed: {err}");
                    break;
                }
            };
            let path = entry.path();
            let is_dir = match entry.file_type().await {
                Ok(ft) => ft.is_dir(),
                Err(_) => false,
            };
            if !is_dir {
                continue;
            }
            total += 1;

            let meta_path = path.join("meta.json");
            let content = match tokio::fs::read_to_string(&meta_path).await {
                Ok(c) => c,
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan_async: failed to read {}: {err}",
                        meta_path.display()
                    );
                    continue;
                }
            };

            let meta: MetaJson = match serde_json::from_str(&content) {
                Ok(v) => v,
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan_async: failed to parse {}: {err}",
                        meta_path.display()
                    );
                    continue;
                }
            };

            if meta.status != "completed" {
                continue;
            }

            let started_at = meta.started_at.unwrap_or_default();
            completed.push((started_at, meta.run_id));
        }

        completed.sort_by(|a, b| b.0.cmp(&a.0));
        let latest_completed = completed.into_iter().next().map(|(_, run_id)| run_id);

        Self {
            latest_completed,
            total,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TempDirGuard(std::path::PathBuf);
    impl Drop for TempDirGuard {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn parse_meta_loose_falls_back_when_run_id_missing() {
        let json = r#"{"status": "completed", "started_at": "2026-01-01T00:00:00Z"}"#;
        let entry = parse_meta_loose(json, "dir-abc").expect("parse should succeed");
        assert_eq!(entry.run_id, "dir-abc");
        assert_eq!(entry.status, "completed");
    }

    #[test]
    fn parse_meta_loose_falls_back_when_status_missing() {
        let json = r#"{"run_id": "abc", "started_at": "2026-01-01T00:00:00Z"}"#;
        let entry = parse_meta_loose(json, "dir-abc").expect("parse should succeed");
        assert_eq!(entry.run_id, "abc");
        assert_eq!(entry.status, "unknown");
    }

    #[test]
    fn parse_meta_loose_falls_back_when_both_missing() {
        let json = r#"{"started_at": "2026-01-01T00:00:00Z"}"#;
        let entry = parse_meta_loose(json, "dir-xyz").expect("parse should succeed");
        assert_eq!(entry.run_id, "dir-xyz");
        assert_eq!(entry.status, "unknown");
        assert_eq!(entry.started_at, "2026-01-01T00:00:00Z");
    }

    #[test]
    fn parse_meta_loose_returns_none_on_invalid_json() {
        assert!(parse_meta_loose("}{not json", "dir").is_none());
    }

    #[tokio::test]
    async fn list_run_buffer_entries_includes_partially_broken_meta() {
        let root = std::env::temp_dir().join(format!("flowsurface-test-{}", uuid::Uuid::new_v4()));
        tokio::fs::create_dir_all(&root).await.unwrap();
        let _guard = TempDirGuard(root.clone());
        let root = root.as_path();

        // run-1: 完全な meta.json
        let r1 = root.join("run-1");
        tokio::fs::create_dir(&r1).await.unwrap();
        tokio::fs::write(
            r1.join("meta.json"),
            r#"{"run_id":"run-1","status":"completed","started_at":"2026-03-01T00:00:00Z"}"#,
        )
        .await
        .unwrap();

        // run-2: run_id 欠落 → ディレクトリ名フォールバックで表示されるべき
        let r2 = root.join("run-2");
        tokio::fs::create_dir(&r2).await.unwrap();
        tokio::fs::write(
            r2.join("meta.json"),
            r#"{"status":"running","started_at":"2026-03-02T00:00:00Z"}"#,
        )
        .await
        .unwrap();

        // run-3: status 欠落 → "unknown" で表示されるべき
        let r3 = root.join("run-3");
        tokio::fs::create_dir(&r3).await.unwrap();
        tokio::fs::write(
            r3.join("meta.json"),
            r#"{"run_id":"run-3","started_at":"2026-03-03T00:00:00Z"}"#,
        )
        .await
        .unwrap();

        let rows = list_run_buffer_entries(root).await;
        let by_id: std::collections::HashMap<_, _> =
            rows.iter().map(|r| (r.run_id.clone(), r.clone())).collect();

        assert_eq!(rows.len(), 3, "all three entries should appear: {rows:?}");
        assert_eq!(by_id["run-1"].status, "completed");
        assert_eq!(by_id["run-2"].status, "running");
        assert_eq!(by_id["run-3"].status, "unknown");
    }
}
