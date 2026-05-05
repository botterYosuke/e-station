/// F7/T6: structural test — keyboard accelerator path checks MODE_SWITCHING.
const NATIVE_MENU_RS: &str = include_str!("../src/native_menu.rs");

#[test]
fn widget_keyboard_subscription_checks_mode_switching() {
    // This test guards against regression — MODE_SWITCHING must be checked
    // inside widget_keyboard_subscription before dispatching SwitchMode.
    let idx = NATIVE_MENU_RS
        .find("fn widget_keyboard_subscription")
        .expect("widget_keyboard_subscription must exist in native_menu.rs");
    let body = &NATIVE_MENU_RS[idx..];
    let end = body[1..].find("\nfn ").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("MODE_SWITCHING"),
        "widget_keyboard_subscription must check MODE_SWITCHING to suppress dispatch during mode switch (統一決定 64)"
    );
}

#[test]
fn mode_switch_state_field_exists_in_flowsurface() {
    // M13: pending_mode_switch + _mode_switch_guard unified into
    // mode_switch_state: Option<(AppMode, ModeSwitchGuard)>.
    const MAIN_RS: &str = include_str!("../src/main.rs");
    assert!(
        MAIN_RS.contains("mode_switch_state"),
        "Flowsurface must have mode_switch_state field for replay→live async wait (M13 unified tuple)"
    );
}

#[test]
fn mode_switch_state_holds_guard() {
    // M13: the unified field must carry the ModeSwitchGuard so that dropping
    // the tuple atomically releases MODE_SWITCHING.
    const MAIN_RS: &str = include_str!("../src/main.rs");
    assert!(
        MAIN_RS.contains("ModeSwitchGuard") && MAIN_RS.contains("mode_switch_state"),
        "mode_switch_state must hold ModeSwitchGuard via tuple; pair drift is impossible by construction (M13)"
    );
}

#[test]
fn keyboard_subscription_checks_mode_switching() {
    // H2 / 統一決定 64: keyboard accelerators fire independently of menu enabled
    // state. The subscription must suppress SwitchMode dispatch while
    // MODE_SWITCHING is true. Applies to the unified widget_keyboard_subscription
    // which runs on all platforms (muda removed).
    let pos = NATIVE_MENU_RS
        .find("fn widget_keyboard_subscription")
        .expect("widget_keyboard_subscription must exist in src/native_menu.rs");
    let end = (pos + 4000).min(NATIVE_MENU_RS.len());
    let mut safe_end = end;
    while !NATIVE_MENU_RS.is_char_boundary(safe_end) {
        safe_end -= 1;
    }
    let body = &NATIVE_MENU_RS[pos..safe_end];
    assert!(
        body.contains("SwitchMode") && body.contains("MODE_SWITCHING"),
        "widget_keyboard_subscription must suppress SwitchMode dispatch when MODE_SWITCHING is true (H2)"
    );
}
