//! F9 R1-H7: `refresh_wandb_auth` must apply a hard timeout (default 7s) and
//! fall back to `WandbAuthState::unauthenticated()` rather than blocking
//! indefinitely on a hung subprocess.
//!
//! The flowsurface bin is not exposed as a library, so we cannot call
//! `refresh_wandb_auth_with_timeout` directly from this integration test.
//! Instead we structurally verify the wiring:
//!  - the const `REFRESH_AUTH_TIMEOUT` is defined and has Duration type,
//!  - `refresh_wandb_auth` body wraps the spawn in `tokio::time::timeout`,
//!  - the timeout error arm returns `WandbAuthState::unauthenticated()`.

use std::fs;
use std::path::Path;

fn read_wandb_auth_src() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/wandb_auth.rs");
    fs::read_to_string(&path).expect("failed to read src/wandb_auth.rs")
}

fn safe_slice(src: &str, mut start: usize, mut end: usize) -> &str {
    if end > src.len() {
        end = src.len();
    }
    while start < src.len() && !src.is_char_boundary(start) {
        start += 1;
    }
    while end > start && !src.is_char_boundary(end) {
        end -= 1;
    }
    &src[start..end]
}

#[test]
fn refresh_auth_timeout_constant_defined() {
    let src = read_wandb_auth_src();
    assert!(
        src.contains("REFRESH_AUTH_TIMEOUT"),
        "src/wandb_auth.rs must define a REFRESH_AUTH_TIMEOUT const (F9 R1-H7)"
    );
    // Duration::from_secs(7) — 7 秒 hard timeout
    assert!(
        src.contains("Duration::from_secs(7)"),
        "REFRESH_AUTH_TIMEOUT must be 7 seconds (F9b-DoD)"
    );
}

#[test]
fn refresh_wandb_auth_uses_tokio_timeout() {
    let src = read_wandb_auth_src();
    let fn_idx = src
        .find("pub(crate) async fn refresh_wandb_auth_with_timeout")
        .expect("refresh_wandb_auth_with_timeout helper must exist (F9 R1-H7)");
    let body = safe_slice(&src, fn_idx, fn_idx + 2000);
    assert!(
        body.contains("tokio::time::timeout"),
        "refresh_wandb_auth_with_timeout must wrap the spawn in tokio::time::timeout (F9 R1-H7) — body: {body}"
    );
    // timeout (Elapsed) arm must fall back to unauthenticated()
    assert!(
        body.contains("unauthenticated()"),
        "timeout arm must return WandbAuthState::unauthenticated() (fail-closed) — body: {body}"
    );
}

#[test]
fn refresh_wandb_auth_delegates_to_helper_with_default_timeout() {
    let src = read_wandb_auth_src();
    let fn_idx = src
        .find("pub async fn refresh_wandb_auth()")
        .expect("refresh_wandb_auth must exist");
    let body = safe_slice(&src, fn_idx, fn_idx + 400);
    assert!(
        body.contains("refresh_wandb_auth_with_timeout"),
        "refresh_wandb_auth must delegate to refresh_wandb_auth_with_timeout(REFRESH_AUTH_TIMEOUT) (F9 R1-H7) — body: {body}"
    );
    assert!(
        body.contains("REFRESH_AUTH_TIMEOUT"),
        "refresh_wandb_auth must pass REFRESH_AUTH_TIMEOUT to the helper — body: {body}"
    );
}

/// F9 R2-H2: when `tokio::time::timeout` elapses, the spawn future is dropped.
/// Without `kill_on_drop(true)`, the underlying `uv run check_auth.py` child
/// is left as an orphan (tokio's Drop impl only sends SIGKILL when the flag
/// is set). Source-inspection guards the wiring.
#[test]
fn refresh_wandb_auth_uses_kill_on_drop() {
    let src = read_wandb_auth_src();
    let fn_idx = src
        .find("pub(crate) async fn refresh_wandb_auth_with_timeout")
        .expect("refresh_wandb_auth_with_timeout helper must exist (F9 R1-H7)");
    let body = safe_slice(&src, fn_idx, fn_idx + 2000);
    assert!(
        body.contains(".kill_on_drop(true)"),
        "F9 R2-H2: the Command builder must call .kill_on_drop(true) so that the \
         child process is killed when tokio::time::timeout elapses and the spawn \
         future is dropped. body: {body}"
    );
}

/// Run a real timeout against a fake hung subprocess via tokio::time::timeout.
/// We cannot invoke `refresh_wandb_auth_with_timeout` directly (bin-only
/// crate), so we sanity-check that `tokio::time::timeout` itself behaves as
/// the helper relies on: a future that never completes elapses to Err.
#[tokio::test]
async fn tokio_timeout_returns_err_on_hang() {
    let pending = std::future::pending::<()>();
    let result = tokio::time::timeout(std::time::Duration::from_millis(50), pending).await;
    assert!(
        result.is_err(),
        "tokio::time::timeout must return Err for a future that never resolves; \
         this is the contract refresh_wandb_auth_with_timeout depends on"
    );
}
