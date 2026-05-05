/// F7/T4: structural test — restart_with_mode() sets APP_MODE then calls restart().
const MAIN_RS: &str = include_str!("../src/main.rs");

#[test]
fn restart_with_mode_exists() {
    assert!(
        MAIN_RS.contains("fn restart_with_mode("),
        "restart_with_mode must exist in src/main.rs"
    );
}

#[test]
fn restart_with_mode_calls_set_app_mode() {
    let idx = MAIN_RS
        .find("fn restart_with_mode(")
        .expect("restart_with_mode must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("set_app_mode("),
        "restart_with_mode must call set_app_mode to update APP_MODE"
    );
}

#[test]
fn restart_with_mode_calls_restart() {
    let idx = MAIN_RS
        .find("fn restart_with_mode(")
        .expect("restart_with_mode must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("self.restart()"),
        "restart_with_mode must delegate to self.restart()"
    );
}

#[test]
fn action_switch_mode_no_longer_stub() {
    // The stub comment must be gone
    assert!(
        !MAIN_RS.contains("F7 stub — mode-switch handling not yet implemented"),
        "Action::SwitchMode stub must be replaced with real implementation"
    );
}

/// F7-bugfix: restart_with_mode must update ProcessManager::mode so that the
/// next Python engine process starts with the correct mode via Hello.
/// Without this, the Python engine stays in replay mode after the Rust-side
/// APP_MODE is switched to Live, causing RequestVenueLogin to be rejected with
/// "RequestVenueLogin not allowed in replay mode".
#[test]
fn restart_with_mode_updates_process_manager_mode() {
    let idx = MAIN_RS
        .find("fn restart_with_mode(")
        .expect("restart_with_mode must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("set_mode("),
        "restart_with_mode must call set_mode() to update ProcessManager mode. \
         Without this the Python engine continues running with self._mode = 'replay' \
         even after APP_MODE is set to Live, causing RequestVenueLogin to be rejected."
    );
}

/// F7-bugfix: restart_with_mode must send Command::Shutdown to the Python engine
/// so the recovery loop restarts it with the updated mode. Without this, the
/// Python engine process continues running indefinitely in the old mode.
#[test]
fn restart_with_mode_sends_shutdown_to_engine() {
    let idx = MAIN_RS
        .find("fn restart_with_mode(")
        .expect("restart_with_mode must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("Command::Shutdown"),
        "restart_with_mode must send Command::Shutdown to trigger Python engine restart \
         with the new mode. The recovery loop in main() will then call manager.start() \
         which reads the updated ProcessManager::mode."
    );
}

/// Source-inspection guard: both `set_app_mode` and `app_mode` must exist.
/// The actual runtime roundtrip (acceptance criteria 2) is covered by the
/// `app_mode_roundtrip_tests` module inside `src/main.rs`.
#[test]
fn set_app_mode_and_app_mode_helpers_exist() {
    assert!(
        MAIN_RS.contains("fn set_app_mode("),
        "set_app_mode must exist in src/main.rs"
    );
    assert!(
        MAIN_RS.contains("fn app_mode()"),
        "app_mode must exist in src/main.rs"
    );
}

/// F7-bugfix (High): `ENGINE_CONNECTION_TX` must be cleared to `None` BEFORE
/// `self.restart()` is called so that `Flowsurface::new()` reads
/// `engine_connection = None`. Without this, the new live-mode UI inherits the
/// old replay-mode engine connection and `RequestVenueLogin` is sent to it
/// during the window before the new `Hello{mode=live}` arrives.
#[test]
fn restart_with_mode_clears_engine_connection_before_restart() {
    let idx = MAIN_RS
        .find("fn restart_with_mode(")
        .expect("restart_with_mode must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];

    let conn_tx_idx = fn_body
        .find("ENGINE_CONNECTION_TX")
        .expect(
            "restart_with_mode must reference ENGINE_CONNECTION_TX to clear the engine connection"
        );
    let restart_idx = fn_body
        .find("self.restart()")
        .expect("restart_with_mode must call self.restart()");
    assert!(
        conn_tx_idx < restart_idx,
        "ENGINE_CONNECTION_TX must be cleared BEFORE self.restart() so \
         Flowsurface::new() reads engine_connection = None. \
         Otherwise RequestVenueLogin races against the old replay-mode engine."
    );
}
