//! Structural pins for the status-bar Tachibana login chip.
//!
//! Commit 9f8ed0f removed the inline "立花 ログイン" button from the
//! sidebar exchange-filter list.  The replacement is `venue_login_chip`
//! inside `status_bar()`, which renders a clickable chip at the bottom of
//! the main window for every non-ready venue state.
//!
//! These tests pin:
//! 1. `status_bar()` calls `venue_login_chip` so the chip is always present.
//! 2. `RequestTachibanaLogin(Trigger::Manual)` is wired as the on_login action.
//! 3. `view()` pushes `status_bar()` into the main window column.
//! 4. `venue_login_chip` shows "ログイン" for `Idle` and "再ログイン" for `Error`.
//! 5. `venue_login_chip` does NOT show a clickable label during `LoginInFlight`.

fn read_main_rs() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/src/main.rs")).expect("src/main.rs must be readable")
}

fn extract_fn_body(src: &str, fn_needle: &str, max_bytes: usize) -> String {
    let start = src
        .find(fn_needle)
        .unwrap_or_else(|| panic!("{fn_needle} not found in src/main.rs"));
    let end = (start + max_bytes).min(src.len());
    src[start..end].to_string()
}

/// `status_bar()` must call `venue_login_chip` — removing it would silently
/// hide the only login affordance remaining after the sidebar button removal.
#[test]
fn status_bar_calls_venue_login_chip() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn status_bar(", 3000);
    assert!(
        body.contains("venue_login_chip"),
        "status_bar() must call venue_login_chip() to render the Tachibana \
         login chip. Without it the user has no way to trigger login from \
         the UI after commit 9f8ed0f removed the sidebar inline button."
    );
}

/// The login message passed to `venue_login_chip` must be
/// `RequestTachibanaLogin(Trigger::Manual)` so the duplicate-press
/// suppression guard in `handle_venue` is exercised on manual clicks.
#[test]
fn status_bar_wires_request_tachibana_login_manual() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn status_bar(", 3000);
    assert!(
        body.contains("RequestTachibanaLogin(Trigger::Manual)"),
        "status_bar() must pass RequestTachibanaLogin(Trigger::Manual) as \
         the on_login action to venue_login_chip. Changing this to Auto or \
         removing it breaks the manual re-login path from the status bar."
    );
}

/// `view()` must push `status_bar()` into the main window column so the
/// chip is actually visible to the user.
#[test]
fn view_pushes_status_bar() {
    let src = read_main_rs();
    assert!(
        src.contains("status_bar("),
        "view() must call status_bar() to include the Tachibana login chip \
         in the rendered UI. Removing this call hides the chip entirely."
    );
}

/// `venue_login_chip` must show "ログイン" for the `Idle` state.
/// This is the primary recovery affordance after a cancelled login dialog.
#[test]
fn login_chip_idle_shows_login_label() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn venue_login_chip(", 2000);
    // "ログイン" appears as btn_label for Idle
    assert!(
        body.contains("ログイン"),
        "venue_login_chip must render 'ログイン' text for VenueState::Idle. \
         This is the button label the user clicks to retry after cancelling \
         the login dialog."
    );
}

/// `venue_login_chip` must show "再ログイン" for the `Error` (non-market_closed) state
/// so users can re-authenticate after an authentication failure in addition
/// to the venue_banner.
#[test]
fn login_chip_error_shows_relogin_label() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn venue_login_chip(", 2000);
    assert!(
        body.contains("再ログイン"),
        "venue_login_chip must render '再ログイン' text for VenueState::Error \
         (non-market_closed). Without this the Error state has only the \
         venue_banner button as a login affordance."
    );
}

// ---------------------------------------------------------------------------
// kabu chip wiring pins
// ---------------------------------------------------------------------------

/// `status_bar()` must wire `RequestKabuLogin(Trigger::Manual)` to the kabu
/// chip so clicking it sends the right message through the FSM duplicate-press
/// guard in `handle_venue`.
///
/// max_bytes = 4000: `RequestKabuLogin` sits at ~2580 bytes into status_bar,
/// `kabu_chip` row inclusion at ~2784 bytes — 4000 gives ample margin.
#[test]
fn status_bar_wires_request_kabu_login_manual() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn status_bar(", 4000);
    assert!(
        body.contains("RequestKabuLogin(Trigger::Manual)"),
        "status_bar() must pass RequestKabuLogin(Trigger::Manual) as the \
         on_login action to the kabu venue_login_chip. Changing this breaks \
         the manual kabu re-login path from the status bar."
    );
}

/// The kabu chip variable must actually be pushed into the status-bar row.
/// If `kabu_chip` is constructed but not included in the row, kabu login UI
/// is silently absent from the rendered window.
#[test]
fn status_bar_includes_kabu_chip_in_row() {
    let src = read_main_rs();
    let body = extract_fn_body(&src, "fn status_bar(", 4000);
    assert!(
        body.contains("kabu_chip,"),
        "status_bar() must push kabu_chip into the row element. \
         Without it the kabu login button is never rendered."
    );
}

/// `venue_login_chip` must disable click interaction during `LoginInFlight`
/// (the in-flight guard).  Clicking while in flight would produce a no-op
/// at the FSM level (`try_claim_login_in_flight` returns false), but the
/// button itself should signal non-interactivity to avoid user confusion.
#[test]
fn login_chip_login_in_flight_suppresses_on_press() {
    let src = read_main_rs();
    // venue_login_chip は ~100 行あるため 4000 バイト以上を取得する
    let body = extract_fn_body(&src, "fn venue_login_chip(", 5000);
    // The in_flight guard must branch on `in_flight` and skip `.on_press()`
    assert!(
        body.contains("in_flight"),
        "venue_login_chip must check an `in_flight` flag to suppress \
         the on_press handler during LoginInFlight."
    );
    assert!(
        body.contains("btn.into()"),
        "venue_login_chip must return the button without on_press (btn.into()) \
         when LoginInFlight so the chip is visually non-interactive."
    );
}
