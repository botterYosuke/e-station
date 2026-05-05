//! W&B 認証状態 — Python subprocess の stdout JSON を Rust 側で保持するだけ。
//! 認証判定ロジック（netrc・env 解決）は Python 側に完全委譲（P9 Q11）。
//
// F9 R1-H8: module-wide `#![allow(dead_code)]` was removed. Per-item allows
// only where strictly needed (e.g. `Default`-style helpers reserved for tests).

use serde::Deserialize;

/// `examples/wandb/check_auth.py` を subprocess で起動し、stdout JSON を
/// `WandbAuthState` にデシリアライズして返す。
///
/// 起動コマンド: `uv run --with wandb python examples/wandb/check_auth.py`
/// 失敗時（spawn 失敗・JSON 不正・timeout）は `WandbAuthState::unauthenticated()` を返す（fail-closed）。
///
/// F9 R1-H7: 7 秒の hard timeout を付与する（F9b-DoD「7 秒以内」を Rust 側で
/// 保護）。timeout 時は `unauthenticated()` を返し warn ログを出す。
pub async fn refresh_wandb_auth() -> WandbAuthState {
    refresh_wandb_auth_with_timeout(REFRESH_AUTH_TIMEOUT).await
}

/// F9 R1-H7: refresh_wandb_auth の hard timeout（既定 7 秒）。テストで短縮できる
/// よう定数として expose する。
pub const REFRESH_AUTH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(7);

/// F9 R1-H7: timeout を引数化した内部実装。テストから 100ms 等の短い timeout
/// を渡して timeout 経路を検証できるようにする。
pub(crate) async fn refresh_wandb_auth_with_timeout(
    timeout: std::time::Duration,
) -> WandbAuthState {
    use tokio::process::Command;

    // F9 R2-H2: enable `kill_on_drop` so that when `tokio::time::timeout`
    // elapses and the spawn future is dropped, tokio sends SIGKILL to the
    // child instead of leaving an orphan check_auth.py process.
    let fut = Command::new("uv")
        .args([
            "run",
            "--with",
            "wandb",
            "python",
            "examples/wandb/check_auth.py",
        ])
        .kill_on_drop(true)
        .output();

    let output = match tokio::time::timeout(timeout, fut).await {
        Ok(Ok(o)) => o,
        Ok(Err(err)) => {
            log::warn!("refresh_wandb_auth: failed to spawn check_auth.py: {err}");
            return WandbAuthState::unauthenticated();
        }
        Err(_elapsed) => {
            log::warn!(
                "refresh_wandb_auth: check_auth.py exceeded {:?} timeout; returning unauthenticated (F9 R1-H7)",
                timeout
            );
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
///
/// F9 R1-M15: `None` → `NotSet` rename。`Option::None` との視覚的混同を避ける。
/// wire 形式は `serde(rename = "none")` で互換維持。
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuthMethod {
    Env,
    Netrc,
    #[serde(rename = "none")]
    NotSet,
}

/// Python `examples/wandb/check_auth.py` が stdout に返す JSON の構造体。
/// Rust 側は判定ロジックを持たず、この struct を受け取ってメニュー状態に流すだけ。
///
/// F9 R1-H9: illegal state（`authenticated=true` ∧ `method=NotSet`）を
/// `try_from = "RawWandbAuthState"` validator で deserialize 段階で reject する。
/// 完全な enum 化（`enum WandbAuthState { Unauthenticated, Authenticated { ... } }`）
/// は破壊的変更のため avoid; impl level で illegal state を拒絶する最小変更。
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(try_from = "RawWandbAuthState")]
pub struct WandbAuthState {
    pub authenticated: bool,
    pub method: AuthMethod,
    pub username: Option<String>,
    pub error: Option<String>,
}

/// Untrusted intermediate for `WandbAuthState` deserialization (F9 R1-H9).
/// `TryFrom` enforces invariant `authenticated=true ⇒ method ≠ NotSet`.
#[derive(Debug, Deserialize)]
struct RawWandbAuthState {
    authenticated: bool,
    method: AuthMethod,
    #[serde(default)]
    username: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

impl TryFrom<RawWandbAuthState> for WandbAuthState {
    type Error = String;

    fn try_from(raw: RawWandbAuthState) -> Result<Self, Self::Error> {
        // illegal state: authenticated implies a concrete auth method.
        if raw.authenticated && matches!(raw.method, AuthMethod::NotSet) {
            return Err(
                "WandbAuthState: authenticated=true requires method ≠ \"none\" (illegal state)"
                    .to_string(),
            );
        }
        Ok(Self {
            authenticated: raw.authenticated,
            method: raw.method,
            username: raw.username,
            error: raw.error,
        })
    }
}

impl WandbAuthState {
    /// fail-closed のデフォルト（未認証）。アプリ起動直後や subprocess 失敗時に使う。
    pub fn unauthenticated() -> Self {
        Self {
            authenticated: false,
            method: AuthMethod::NotSet,
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
///
/// F9 R1-M9: fields are `pub(crate)` because flowsurface is a bin-only crate
/// and these fields are accessed only from within the binary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunBufferEntry {
    pub(crate) run_id: String,
    pub(crate) status: String,
    pub(crate) started_at: String,
    pub(crate) strategy_file: Option<String>,
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
    // F9 R1-H2: continue on transient `next_entry` errors instead of `break`,
    // so a single unreadable directory entry does not truncate the whole
    // history list. Bounded by an upper attempt counter to avoid pathological
    // infinite loops if the kernel keeps returning errors.
    let mut consecutive_errors = 0usize;
    const MAX_CONSECUTIVE_ERRORS: usize = 64;
    loop {
        let entry = match entries.next_entry().await {
            Ok(Some(e)) => {
                consecutive_errors = 0;
                e
            }
            Ok(None) => break,
            Err(err) => {
                log::warn!("list_run_buffer_entries: next_entry failed: {err}");
                consecutive_errors += 1;
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS {
                    log::warn!(
                        "list_run_buffer_entries: aborting after {consecutive_errors} consecutive next_entry errors"
                    );
                    break;
                }
                continue;
            }
        };
        let path = entry.path();
        // F9 R2-M2: log file_type errors instead of silently dropping the
        // entry. `unwrap_or(false)` would treat read-permission errors as
        // "not a directory" and hide them.
        let is_dir = match entry.file_type().await {
            Ok(ft) => ft.is_dir(),
            Err(err) => {
                log::warn!(
                    "list_run_buffer_entries: file_type failed for {}: {err}",
                    path.display()
                );
                false
            }
        };
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
    /// `RunBufferIndex` は完了 run id のみ計算するため scan 経路では未読。
    /// `parse_meta_loose` 側で履歴行に展開される (F9 R1-H8 retains for forward use).
    #[serde(default)]
    #[allow(dead_code)]
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
    ///
    // F9 R2-M5: startup も `scan_async` 経由に切り替えたため caller ゼロになった
    // が、CLI/diagnostic から同期で呼びたい場面のために残す。
    // reason: kept for diagnostic / CLI use; runtime path uses `scan_async`.
    #[allow(dead_code)]
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

            // F9 R2-M4: skip completed runs that lack a usable `started_at`.
            // The previous `unwrap_or_default()` produced an empty string and
            // could let a started_at-less run win the descending sort when
            // all candidates were missing the field, returning a
            // non-deterministic "latest_completed". Treat missing/empty as
            // not-rankable.
            let started_at = match meta.started_at {
                Some(s) if !s.trim().is_empty() => s,
                _ => continue,
            };
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

        // F9 R2-M1: continue on transient `next_entry` errors so a single
        // unreadable entry does not silently truncate the scan and miss the
        // latest_completed run. Mirrors `list_run_buffer_entries`.
        let mut consecutive_errors = 0usize;
        const MAX_CONSECUTIVE_ERRORS: usize = 64;
        loop {
            let entry = match entries.next_entry().await {
                Ok(Some(e)) => {
                    consecutive_errors = 0;
                    e
                }
                Ok(None) => break,
                Err(err) => {
                    log::warn!("RunBufferIndex::scan_async: next_entry failed: {err}");
                    consecutive_errors += 1;
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS {
                        log::warn!(
                            "RunBufferIndex::scan_async: aborting after {consecutive_errors} consecutive next_entry errors"
                        );
                        break;
                    }
                    continue;
                }
            };
            let path = entry.path();
            // F9 R2-M2: log file_type errors instead of silently treating the
            // entry as a non-directory. Unreadable file_type is a real FS
            // anomaly that should be visible.
            let is_dir = match entry.file_type().await {
                Ok(ft) => ft.is_dir(),
                Err(err) => {
                    log::warn!(
                        "RunBufferIndex::scan_async: file_type failed for {}: {err}",
                        path.display()
                    );
                    false
                }
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

            // F9 R2-M4: mirror sync scan; skip completed runs without a
            // usable started_at to keep latest_completed deterministic.
            let started_at = match meta.started_at {
                Some(s) if !s.trim().is_empty() => s,
                _ => continue,
            };
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

    // F9 R2-H3: validator tests for `WandbAuthState::try_from` live here in
    // the bin crate (rather than `tests/wandb_auth_state.rs`) because
    // `tests/` is an integration-test crate and cannot import the real
    // `WandbAuthState` from a bin-only crate. The integration file uses a
    // local mirror struct that lacks the `try_from` validator, so this is
    // the only place where the actual `RawWandbAuthState` -> `WandbAuthState`
    // illegal-state rejection is exercised.
    // reason: validator must be reachable; do not move out of this module.
    #[test]
    fn illegal_state_authenticated_true_method_none_is_rejected() {
        let json = r#"{"authenticated": true, "method": "none", "username": null, "error": null}"#;
        let result: Result<WandbAuthState, _> = serde_json::from_str(json);
        assert!(
            result.is_err(),
            "authenticated=true with method=none must be rejected (F9 R1-H9 / R2-H3)"
        );
    }

    #[test]
    fn legal_state_authenticated_false_method_none_is_accepted() {
        let json = r#"{"authenticated": false, "method": "none", "username": null, "error": null}"#;
        let state: WandbAuthState = serde_json::from_str(json).expect("must deserialize");
        assert!(!state.authenticated);
        assert!(matches!(state.method, AuthMethod::NotSet));
    }

    #[test]
    fn legal_state_authenticated_false_method_env_is_accepted() {
        let json = r#"{"authenticated": false, "method": "env", "username": null, "error": "viewer_lookup_timeout"}"#;
        let state: WandbAuthState = serde_json::from_str(json).expect("must deserialize");
        assert!(!state.authenticated);
        assert!(matches!(state.method, AuthMethod::Env));
        assert_eq!(state.error.as_deref(), Some("viewer_lookup_timeout"));
    }

    #[test]
    fn legal_state_authenticated_true_method_env_is_accepted() {
        let json =
            r#"{"authenticated": true, "method": "env", "username": "alice", "error": null}"#;
        let state: WandbAuthState = serde_json::from_str(json).expect("must deserialize");
        assert!(state.authenticated);
        assert!(matches!(state.method, AuthMethod::Env));
        assert_eq!(state.username.as_deref(), Some("alice"));
    }

    #[test]
    fn legal_state_authenticated_true_when_method_is_credential_file() {
        let json =
            r#"{"authenticated": true, "method": "netrc", "username": "bob", "error": null}"#;
        let state: WandbAuthState = serde_json::from_str(json).expect("must deserialize");
        assert!(state.authenticated);
        assert!(matches!(state.method, AuthMethod::Netrc));
        assert_eq!(state.username.as_deref(), Some("bob"));
    }

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

    /// F9 R1-H2 regression: `list_run_buffer_entries` must `continue` (not
    /// `break`) on transient errors so a single unreadable entry does not
    /// truncate the rest of the history. Exact error-injection is hard via
    /// tokio::fs without a custom FS, so we structurally guard the source.
    #[test]
    fn list_run_buffer_entries_continues_on_next_entry_error() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/wandb_auth.rs");
        let src = std::fs::read_to_string(path).expect("read wandb_auth.rs");
        let fn_idx = src
            .find("pub async fn list_run_buffer_entries")
            .expect("list_run_buffer_entries must exist");
        let body_end = (fn_idx + 4000).min(src.len());
        let mut safe = body_end;
        while !src.is_char_boundary(safe) {
            safe -= 1;
        }
        let body = &src[fn_idx..safe];
        // The next_entry error arm must contain `continue`; the previous
        // implementation used `break` and silently dropped subsequent rows.
        let next_entry_idx = body
            .find("next_entry().await")
            .expect("body must call entries.next_entry().await");
        let arm = &body[next_entry_idx..(next_entry_idx + 800).min(body.len())];
        assert!(
            arm.contains("continue"),
            "list_run_buffer_entries' next_entry error arm must `continue` (F9 R1-H2) — arm: {arm}"
        );
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
