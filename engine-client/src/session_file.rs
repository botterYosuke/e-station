/// Engine session file — written atomically after handshake, deleted on Drop.
///
/// Python helper reads this file to obtain the `token` and `port` that the
/// Rust supervisor generated when spawning the engine. The file lives at
/// `data_path("engine-session.json")` (Windows: `%APPDATA%\flowsurface\`).
use std::fmt;
use std::path::Path;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Snapshot of the engine session written to disk after a successful handshake.
///
/// The `token` field is `pub(crate)` to discourage external code from
/// reading or logging the secret. `Debug` is implemented manually so that
/// accidental `dbg!()` / `{:?}` calls never leak the token value.
#[derive(Serialize, Deserialize, Clone)]
pub struct EngineSession {
    pub port: u16,
    /// Raw token bytes (hex-encoded). Never log this value — `Debug` redacts it.
    pub(crate) token: String,
    pub pid: u32,
    pub schema_major: u32,
    /// ISO-8601 UTC timestamp captured at write time. Helps the Python helper
    /// detect stale session files when the supervisor PID was reused.
    pub started_at: DateTime<Utc>,
    /// Transport protocol in use: `"ws"` (WebSocket) or `"grpc"`.
    /// G0.9: present in all new sessions; old session files without this field
    /// default to `"ws"` via `#[serde(default)]`.
    #[serde(default = "default_transport")]
    pub transport: String,
}

fn default_transport() -> String {
    "ws".to_string()
}

impl fmt::Debug for EngineSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("EngineSession")
            .field("port", &self.port)
            .field("token", &"[REDACTED]")
            .field("pid", &self.pid)
            .field("schema_major", &self.schema_major)
            .field("started_at", &self.started_at)
            .field("transport", &self.transport)
            .finish()
    }
}

impl EngineSession {
    /// Create a new WebSocket session with `started_at = now()`.
    ///
    /// Defaults `transport` to `"ws"`. Use [`Self::with_transport`] when
    /// creating a gRPC session.
    pub fn new(port: u16, token: String, pid: u32, schema_major: u32) -> Self {
        Self::with_transport(port, token, pid, schema_major, "ws")
    }

    /// Create a new session with an explicit transport tag (`"ws"` or `"grpc"`).
    pub fn with_transport(
        port: u16,
        token: String,
        pid: u32,
        schema_major: u32,
        transport: impl Into<String>,
    ) -> Self {
        Self {
            port,
            token,
            pid,
            schema_major,
            transport: transport.into(),
            started_at: Utc::now(),
        }
    }

    /// Write session data to `path` atomically via a `.tmp` rename.
    ///
    /// Creates parent directories if they do not exist.
    /// On Unix, the tmp file is chmod'd to 0o600 before the rename.
    pub fn write_atomic(&self, path: &Path) -> std::io::Result<()> {
        let json = serde_json::to_string(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        // Ensure parent directory exists.
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Write to a sibling .tmp file, then rename for atomicity.
        let tmp_path = path.with_extension("json.tmp");
        std::fs::write(&tmp_path, json.as_bytes())?;

        // Linux/macOS: restrict to owner read/write only.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Err(e) =
                std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o600))
            {
                // M-5 (silent): rename していない tmp を残さない。
                let _ = std::fs::remove_file(&tmp_path);
                return Err(e);
            }
        }

        if let Err(e) = std::fs::rename(&tmp_path, path) {
            // M-5 (silent): rename 失敗時に .tmp を残さない。
            let _ = std::fs::remove_file(&tmp_path);
            return Err(e);
        }
        Ok(())
    }

    /// Remove the session file, ignoring errors (file may already be gone).
    pub fn delete(path: &Path) {
        let _ = std::fs::remove_file(path);
    }

    /// C3: remove a stale session file left behind by a crashed engine.
    ///
    /// Behaviour:
    /// - Missing file → no-op.
    /// - Unreadable / invalid JSON → delete (it's corrupt residue).
    /// - Valid JSON whose `pid` is not live → delete.
    /// - Valid JSON whose `pid` is live → keep (the live engine owns it; the
    ///   subsequent atomic write from the new spawn will overwrite anyway, but
    ///   we don't want to introduce a window where the live engine's session
    ///   appears to be missing).
    ///
    /// Called from the spawn path before launching a fresh Python engine so
    /// orphaned files from a previous crash don't fool the helper into
    /// attaching to a dead pid.
    pub fn reap_stale(path: &Path) {
        if !path.exists() {
            return;
        }
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(_) => {
                // Unreadable — treat as corrupt and remove.
                let _ = std::fs::remove_file(path);
                return;
            }
        };
        let parsed: Result<EngineSession, _> = serde_json::from_slice(&bytes);
        let Ok(session) = parsed else {
            // Corrupt JSON: drop it.
            let _ = std::fs::remove_file(path);
            return;
        };
        if !pid_is_live(session.pid) {
            let _ = std::fs::remove_file(path);
        }
    }
}

/// True iff a process with `pid` currently exists on the system.
/// `pid == 0` is treated as not-live (sentinel from when `Child::id()` returned None).
///
/// Stability: on Windows the sysinfo snapshot occasionally misses a freshly
/// targeted pid in the first refresh due to internal caching/timing in
/// `NtQuerySystemInformation`. We perform up to 3 refreshes (50 ms apart)
/// before reporting "not live" so the ambient flake in
/// `reap_stale_keeps_live_pid_file` cannot turn a live engine pid into
/// a "stale" verdict and cause the live engine-session.json to be
/// deleted out from under a running engine.
fn pid_is_live(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    use sysinfo::{Pid, ProcessesToUpdate, System};
    let target = Pid::from_u32(pid);
    let mut sys = System::new();
    for attempt in 0..3 {
        // sysinfo 0.32: refresh just the targeted pid; do not prune others.
        sys.refresh_processes(ProcessesToUpdate::Some(&[target]), false);
        if sys.process(target).is_some() {
            return true;
        }
        if attempt < 2 {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Stability guard for `pid_is_live`: the calling process is, by definition,
    /// live. Calling `pid_is_live(std::process::id())` 10 times in a row must
    /// yield `true` every time. If sysinfo's snapshot occasionally misses the
    /// pid (Windows flake) the retry loop inside `pid_is_live` must absorb it.
    #[test]
    fn pid_is_live_for_self_is_stable() {
        let me = std::process::id();
        for i in 0..10 {
            assert!(
                pid_is_live(me),
                "pid_is_live(self) returned false on iteration {i} — \
                 retry loop is not absorbing sysinfo flake"
            );
        }
    }

    #[test]
    fn pid_is_live_zero_is_not_live() {
        assert!(!pid_is_live(0));
    }
}
