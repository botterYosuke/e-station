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
