/// F7/T6: structural test — linux keyboard accelerator path checks MODE_SWITCHING.
const NATIVE_MENU_RS: &str = include_str!("../src/native_menu.rs");

#[test]
fn linux_keyboard_subscription_checks_mode_switching() {
    // Agent A already added MODE_SWITCHING check in linux_keyboard_subscription().
    // This test guards against regression.
    let idx = NATIVE_MENU_RS
        .find("fn linux_keyboard_subscription")
        .expect("linux_keyboard_subscription must exist in native_menu.rs");
    let body = &NATIVE_MENU_RS[idx..];
    let end = body[1..]
        .find("\nfn ")
        .map(|i| i + 1)
        .unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("MODE_SWITCHING"),
        "linux_keyboard_subscription must check MODE_SWITCHING to suppress dispatch during mode switch (統一決定 64)"
    );
}

#[test]
fn pending_mode_switch_field_exists_in_flowsurface() {
    const MAIN_RS: &str = include_str!("../src/main.rs");
    assert!(
        MAIN_RS.contains("pending_mode_switch"),
        "Flowsurface must have pending_mode_switch field for replay→live async wait"
    );
}

#[test]
fn mode_switch_guard_field_exists_in_flowsurface() {
    const MAIN_RS: &str = include_str!("../src/main.rs");
    assert!(
        MAIN_RS.contains("_mode_switch_guard"),
        "Flowsurface must have _mode_switch_guard field to hold ModeSwitchGuard alive during async switch"
    );
}
