//! Pin for invariant **T35-H5-PathFidelity**.
//!
//! `EngineCommand::Bundled(p).program()` must return the path losslessly
//! (as `&OsStr`) so paths containing characters outside the current
//! locale (e.g. Japanese under a Windows user folder) are not silently
//! replaced by the `"flowsurface-engine"` fallback. The previous
//! implementation went `path.to_str().unwrap_or("flowsurface-engine")`,
//! which would mask any non-UTF-8 path component as a missing binary.
//!
//! See `docs/plan/✅tachibana/implementation-plan-T3.5.md` §3 Step B (H5).

use std::ffi::OsStr;
use std::path::PathBuf;

use flowsurface_engine_client::EngineCommand;

#[test]
fn bundled_program_preserves_unicode_path() {
    // Mimics the typical Windows user-profile install path on a Japanese
    // locale: `C:\Users\日本語\flowsurface-engine.exe`. `to_str()` does
    // succeed for valid UTF-8/Unicode here, but we want the returned
    // value to be the *exact* OsStr bytes regardless — no lossy hop
    // through `&str`.
    let path = PathBuf::from(r"C:\Users\日本語\flowsurface-engine.exe");
    let cmd = EngineCommand::Bundled(path.clone());

    let program: &OsStr = cmd.program();
    assert_eq!(
        program,
        path.as_os_str(),
        "Bundled program() must return the original OsStr verbatim, \
         not a lossy fallback"
    );
}

#[test]
fn bundled_program_preserves_utf8_path() {
    // Non-Windows-style POSIX path with a multi-byte filename. Same
    // contract: `program()` returns the original OsStr.
    let path = PathBuf::from("/opt/フローサーフェス/engine");
    let cmd = EngineCommand::Bundled(path.clone());

    let program: &OsStr = cmd.program();
    assert_eq!(program, path.as_os_str());
}

#[test]
fn system_program_returns_program_string() {
    // The `System` variant continues to return its `program` field
    // verbatim (still as `OsStr`, since `&str: AsRef<OsStr>`).
    let cmd = EngineCommand::System {
        program: "python3".to_string(),
        args: vec!["-m".to_string(), "engine".to_string()],
    };

    let program: &OsStr = cmd.program();
    assert_eq!(program, OsStr::new("python3"));
}
