/// Engine session file — written atomically after handshake, deleted on Drop.
///
/// Python helper reads this file to obtain the `token` and `port` that the
/// Rust supervisor generated when spawning the engine. The file lives at
/// `data_path("engine-session.json")` (Windows: `%APPDATA%\flowsurface\`).
use std::path::PathBuf;

/// Snapshot of the engine session written to disk after a successful handshake.
pub struct EngineSession {
    pub port: u16,
    /// Raw token bytes (hex-encoded). Never log this value.
    pub token: String,
    pub pid: u32,
    pub schema_major: u32,
}

impl EngineSession {
    /// Write session data to `path` atomically via a `.tmp` rename.
    ///
    /// Creates parent directories if they do not exist.
    /// On Unix, the tmp file is chmod'd to 0o600 before the rename.
    pub fn write_atomic(&self, path: &PathBuf) -> std::io::Result<()> {
        let started_at = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();

        // Build JSON manually to avoid pulling in an extra serde derive just
        // for a one-shot write. The fields are all primitive types so escaping
        // is not a concern.
        let json = format!(
            r#"{{"port":{},"token":"{}","pid":{},"schema_major":{},"started_at":"{}"}}"#,
            self.port, self.token, self.pid, self.schema_major, started_at
        );

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
            std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o600))?;
        }

        std::fs::rename(&tmp_path, path)?;
        Ok(())
    }

    /// Remove the session file, ignoring errors (file may already be gone).
    pub fn delete(path: &PathBuf) {
        let _ = std::fs::remove_file(path);
    }
}
