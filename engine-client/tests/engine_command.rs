/// Tests for `EngineCommand::resolve` — locates the engine executable next to
/// the running binary, or falls back to `python -m engine` for dev installs.
use flowsurface_engine_client::process::EngineCommand;
use std::fs;

#[test]
fn resolves_bundled_binary_next_to_exe() {
    let tmp = tempdir();
    let exe_name = if cfg!(windows) {
        "flowsurface-engine.exe"
    } else {
        "flowsurface-engine"
    };
    let bundled = tmp.path().join(exe_name);
    fs::write(&bundled, b"#!stub\n").expect("write stub");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut p = fs::metadata(&bundled).unwrap().permissions();
        p.set_mode(0o755);
        fs::set_permissions(&bundled, p).unwrap();
    }

    let cmd = EngineCommand::resolve_with(Some(tmp.path()), None)
        .expect("bundled binary should be discovered");
    match cmd {
        EngineCommand::Bundled(path) => assert_eq!(path, bundled),
        other => panic!("expected Bundled, got {other:?}"),
    }
}

#[test]
fn falls_back_to_python_module_when_no_bundle() {
    let tmp = tempdir();
    // No bundled binary in tmp.path().
    let cmd = EngineCommand::resolve_with(Some(tmp.path()), None).expect("system python fallback");
    match cmd {
        EngineCommand::System { program, args } => {
            assert_eq!(program, "python");
            assert_eq!(args, vec!["-m".to_string(), "engine".to_string()]);
        }
        other => panic!("expected System, got {other:?}"),
    }
}

#[test]
fn explicit_override_takes_precedence() {
    let tmp = tempdir();
    let bundled = tmp.path().join(if cfg!(windows) {
        "flowsurface-engine.exe"
    } else {
        "flowsurface-engine"
    });
    fs::write(&bundled, b"stub").unwrap();

    let override_path = tmp.path().join("custom-engine");
    fs::write(&override_path, b"stub").unwrap();

    let cmd = EngineCommand::resolve_with(Some(tmp.path()), Some(&override_path)).unwrap();
    match cmd {
        EngineCommand::Bundled(path) => assert_eq!(path, override_path),
        other => panic!("expected Bundled override, got {other:?}"),
    }
}

#[test]
fn python_interpreter_override_runs_engine_module() {
    let tmp = tempdir();
    let py_name = if cfg!(windows) { "python.exe" } else { "python3" };
    let py_path = tmp.path().join(py_name);
    fs::write(&py_path, b"stub").unwrap();

    let cmd = EngineCommand::resolve_with(None, Some(&py_path)).unwrap();
    match cmd {
        EngineCommand::System { program, args } => {
            assert_eq!(program, py_path.to_string_lossy());
            assert_eq!(args, vec!["-m".to_string(), "engine".to_string()]);
        }
        other => panic!("expected System (python -m engine), got {other:?}"),
    }
}

#[test]
fn non_python_override_runs_as_bundled_binary() {
    let tmp = tempdir();
    let exe_name = if cfg!(windows) {
        "my-engine.exe"
    } else {
        "my-engine"
    };
    let path = tmp.path().join(exe_name);
    fs::write(&path, b"stub").unwrap();

    let cmd = EngineCommand::resolve_with(None, Some(&path)).unwrap();
    match cmd {
        EngineCommand::Bundled(p) => assert_eq!(p, path),
        other => panic!("expected Bundled, got {other:?}"),
    }
}

// ── tiny tempdir helper to avoid pulling in the `tempfile` crate ──────────────

struct TmpDir(std::path::PathBuf);
impl TmpDir {
    fn path(&self) -> &std::path::Path {
        &self.0
    }
}
impl Drop for TmpDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn tempdir() -> TmpDir {
    let mut p = std::env::temp_dir();
    let nonce: u64 = rand_u64();
    p.push(format!("flowsurface-engine-cmd-test-{nonce:016x}"));
    fs::create_dir_all(&p).expect("create tmp dir");
    TmpDir(p)
}

fn rand_u64() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id() as u128;
    ((nanos.wrapping_mul(1469598103934665603)) ^ (pid << 32)) as u64
}
