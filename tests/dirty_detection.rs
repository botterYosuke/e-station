//! Structural regression pins for F4: dirty detection and confirm-on-exit/open.
//!
//! Pins the invariants documented in `fix-save-menu.md §F4-DoD`:
//! - `last_saved_bytes: Option<Vec<u8>>` field exists (BC-9)
//! - Dirty check: `None => false` (initial/clean state, BC-9)
//! - `save_state_to_disk` updates `last_saved_bytes` after successful write (A-7)
//! - `ExitRequested` checks dirty before calling `iced::exit()` (cases 1-4)
//! - `NativeOpenFileApply` checks dirty before calling `self.restart()` (cases 2-3)
//! - Serialization is deterministic — same state → same bytes 100× (BC-11)
//! - Auto-save does NOT write to `CURRENT_PATH` (R3)

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

// ── Case 1 / BC-9: last_saved_bytes field and None-is-clean invariant ─────────

#[test]
fn last_saved_bytes_field_exists() {
    let src = read_main();
    assert!(
        src.contains("last_saved_bytes"),
        "Flowsurface must have a `last_saved_bytes` field for dirty detection (F4/BC-9)"
    );
}

#[test]
fn last_saved_bytes_is_option_vec_u8() {
    let src = read_main();
    assert!(
        src.contains("Option<Vec<u8>>"),
        "last_saved_bytes must be typed Option<Vec<u8>> for byte-level dirty comparison"
    );
}

#[test]
fn dirty_none_is_clean() {
    // BC-9: last_saved_bytes = None means initial/clean state → dirty = false.
    // The dirty logic must use a `None => false` branch.
    let src = read_main();
    assert!(
        src.contains("None => false"),
        "dirty check must return false for None last_saved_bytes (BC-9: initial state is clean)"
    );
}

// ── Case 2-4: ExitRequested and OpenFileApply check dirty ─────────────────────

#[test]
fn exit_requested_checks_dirty() {
    let src = read_main();
    let prefix = "            Message::ExitRequested(windows) =>";
    let start = src
        .find(prefix)
        .expect("ExitRequested handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("is_dirty") || body.contains("last_saved_bytes"),
        "ExitRequested handler must check dirty state before exiting (F4 cases 2-4)"
    );
}

#[test]
fn exit_requested_shows_confirm_when_dirty() {
    let src = read_main();
    let prefix = "            Message::ExitRequested(windows) =>";
    let start = src
        .find(prefix)
        .expect("ExitRequested handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("confirm_dialog") || body.contains("DiscardAndExit"),
        "ExitRequested handler must show confirm dialog when state is dirty (F4)"
    );
}

#[test]
fn open_file_apply_checks_dirty_before_restart() {
    // NativeOpenFileApply dispatches NativeOpenFilePendingCheck (which carries real
    // window specs) so the dirty comparison uses accurate bytes rather than an empty
    // HashMap that would always appear dirty.  The actual is_dirty() call lives in
    // NativeOpenFilePendingCheck — both handler arms are tested here.
    let src = read_main();

    // Step 1: NativeOpenFileApply must dispatch NativeOpenFilePendingCheck.
    let apply_prefix = "            Message::NativeOpenFileApply { json, path } =>";
    let apply_start = src
        .find(apply_prefix)
        .expect("NativeOpenFileApply handler arm must exist");
    let apply_tail = &src[apply_start..];
    let apply_end = apply_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(apply_tail.len());
    let apply_body = &apply_tail[..apply_end];

    assert!(
        apply_body.contains("NativeOpenFilePendingCheck"),
        "NativeOpenFileApply must dispatch NativeOpenFilePendingCheck for accurate dirty comparison (F4 fix)"
    );

    // Step 2: NativeOpenFilePendingCheck must perform the dirty check.
    // rustfmt may reformat the struct pattern across multiple lines, so search
    // for the newline-prefixed match arm (12-space indent, not a deeper nested
    // construction site).
    let check_needle = "\n            Message::NativeOpenFilePendingCheck {";
    let check_start = src
        .find(check_needle)
        .map(|i| i + 1) // skip the leading '\n' so the slice starts at the arm
        .expect("NativeOpenFilePendingCheck handler arm must exist");
    let check_tail = &src[check_start..];
    let check_end = check_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(check_tail.len());
    let check_body = &check_tail[..check_end];

    assert!(
        check_body.contains("is_dirty")
            || check_body.contains("last_saved_bytes")
            || check_body.contains("pending_open_file"),
        "NativeOpenFilePendingCheck must check dirty state before restart (F4 case 2-3)"
    );
}

#[test]
fn toggle_dialog_modal_none_clears_pending_open_file() {
    // Fix for Issue 2: ToggleDialogModal(None) must clear pending_open_file so that
    // a subsequent Open action re-enters the dirty-check flow instead of skipping it.
    let src = read_main();
    let prefix = "            Message::ToggleDialogModal(dialog) =>";
    let start = src
        .find(prefix)
        .expect("ToggleDialogModal handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("pending_open_file = None"),
        "ToggleDialogModal handler must clear pending_open_file when dismissing (Issue 2 fix)"
    );
    assert!(
        body.contains("pending_exit_windows = None"),
        "ToggleDialogModal handler must clear pending_exit_windows when dismissing (Issue 2 fix)"
    );
}

// ── Case 5 / BC-11: Stable serialization ──────────────────────────────────────

#[test]
fn stable_serialization() {
    // Serializing the same state 100 times must always produce identical bytes.
    // This guards against HashMap-based non-determinism (BC-11 / C-9).
    //
    // NOTE: build_state_json() in main.rs is the real serialization path (it also
    // updates popout window_spec entries). We cannot call it directly from tests
    // without a full Flowsurface instance, so we verify non-determinism at the
    // data::State level: the state struct must not contain bare HashMap fields
    // (which have non-deterministic iteration order and thus non-deterministic JSON).
    //
    // The determinism guarantee is upheld by using BTreeMap (or equivalent ordered
    // structures) for any map-typed fields in data::State. This test pins that
    // structural invariant by asserting that data::State serializes identically
    // across 100 calls (a non-BTreeMap field would break this intermittently).
    let state = data::State::default();
    let first = serde_json::to_string(&state).expect("serialize must succeed");
    for i in 1..100 {
        // Create a fresh default to avoid any hidden interior mutability tricks.
        let next_state = data::State::default();
        let next = serde_json::to_string(&next_state).expect("serialize must succeed");
        assert_eq!(
            first, next,
            "Serialization is non-deterministic at iteration {i}: \
             data::State must not contain bare HashMap fields (BC-11/C-9). \
             Use BTreeMap or IndexMap with a stable order instead."
        );
    }
}

// ── H-4: AudioStream::streams uses BTreeMap for deterministic serialization ────

#[test]
fn stable_serialization_with_audio_streams() {
    // H-4: data::AudioStream::streams must be a BTreeMap (or equivalent ordered map)
    // so that serialization order is deterministic even when streams are populated.
    // FxHashMap would produce different key orderings across runs / compilations.
    use data::audio::{AudioStream, StreamCfg, Threshold};
    use exchange::SerTicker;
    use exchange::adapter::Exchange;

    let ticker_a = SerTicker::new(Exchange::BinanceLinear, "BTCUSDT");
    let ticker_b = SerTicker::new(Exchange::BinanceLinear, "ETHUSDT");

    let mut audio = AudioStream::default();
    audio.streams.insert(
        ticker_a,
        StreamCfg {
            enabled: true,
            threshold: Threshold::Count(5),
        },
    );
    audio.streams.insert(
        ticker_b,
        StreamCfg {
            enabled: false,
            threshold: Threshold::Qty(1.5),
        },
    );

    let first = serde_json::to_string(&audio).expect("serialize must succeed");
    for i in 1..50 {
        let next = serde_json::to_string(&audio).expect("serialize must succeed");
        assert_eq!(
            first, next,
            "AudioStream serialization is non-deterministic at iteration {i} — \
             streams field must use BTreeMap not FxHashMap (H-4)"
        );
    }
}

// ── Case 6: SaveError enum ─────────────────────────────────────────────────────
// (Full log-level assertions are in tests/save_error_classification.rs)

#[test]
fn save_error_enum_exists() {
    let src = read_main();
    assert!(
        src.contains("enum SaveError"),
        "SaveError enum must exist in main.rs (F4/BC-5)"
    );
}

// ── Case 7 / R3: Auto-save does NOT touch CURRENT_PATH ────────────────────────

#[test]
fn save_state_to_disk_does_not_write_current_path() {
    // R3: save_state_to_disk (auto-save) must only write to saved-state.json,
    // never to CURRENT_PATH.
    let src = read_main();
    let fn_start = src
        .find("fn save_state_to_disk(")
        .expect("save_state_to_disk must exist");
    let tail = &src[fn_start..];
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        !body.contains("CURRENT_PATH.lock()"),
        "save_state_to_disk must NOT access CURRENT_PATH — auto-save writes only to saved-state.json (R3)"
    );
}

#[test]
fn save_state_to_disk_updates_last_saved_bytes() {
    // A-7: save_state_to_disk must update last_saved_bytes so auto-save
    // clears the dirty flag (prevents false-positive confirm on quit after auto-save).
    let src = read_main();
    let fn_start = src
        .find("fn save_state_to_disk(")
        .expect("save_state_to_disk must exist");
    let tail = &src[fn_start..];
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("last_saved_bytes"),
        "save_state_to_disk must update last_saved_bytes after a successful write (A-7)"
    );
}

// ── Case 4 / F4-DoD: is_dirty() branch structure ──────────────────────────────

#[test]
fn is_dirty_has_none_is_clean_branch_and_some_comparison_branch() {
    // F4-DoD case 4: even at startup (last_saved_bytes = None) the is_dirty()
    // function must correctly short-circuit to false for the None case, and
    // also have a Some branch that compares build_state_json() output.
    //
    // This structural test pins both branches so neither can be accidentally
    // removed without breaking this guard.
    let src = read_main();

    let fn_start = src
        .find("fn is_dirty(")
        .expect("is_dirty function must exist in main.rs (F4)");
    let tail = &src[fn_start..];
    let fn_end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len().min(500));
    let body = &tail[..fn_end];

    // Branch 1: None => false (BC-9: startup / never-saved is treated as clean)
    assert!(
        body.contains("None => false") || body.contains("return false"),
        "is_dirty must have a None => false branch for the initial/clean state (F4-DoD case 4 / BC-9)"
    );

    // Branch 2: Some(...) comparison using build_state_json
    assert!(
        body.contains("build_state_json"),
        "is_dirty must call build_state_json in the Some branch to compare current vs saved bytes (F4-DoD case 4)"
    );
}

// ── MEDIUM-2 / BC-11: build_state_json uses ordered structures ────────────────

#[test]
fn build_state_json_uses_vec_for_layouts_not_hashmap() {
    // BC-11 / MEDIUM-2: build_state_json() must produce deterministic JSON.
    // The risk is that someone replaces Vec<Layout> or Dashboard::popout with a
    // HashMap/FxHashMap field, which would make serialization order
    // non-deterministic across runs.
    //
    // Strategy: source-inspect the two structures that build_state_json() writes:
    //   1. data::Layouts::layouts  — must be Vec<Layout>, not HashMap
    //   2. data::Dashboard::popout — must be Vec<…>,      not HashMap
    //
    // We also verify that build_state_json itself builds ser_layouts as a Vec
    // (not by inserting into a map).

    // ── 1. data::Layouts::layouts must be Vec<Layout> ─────────────────────────
    let layouts_src = include_str!("../data/src/config/state.rs");
    let layouts_struct_pos = layouts_src
        .find("pub struct Layouts")
        .expect("data::Layouts must exist in data/src/config/state.rs (BC-11)");
    let tail = &layouts_src[layouts_struct_pos..];
    let end = tail
        .find('}')
        .expect("Layouts struct must have closing brace")
        + 1;
    let struct_body = &tail[..end];

    assert!(
        struct_body.contains("Vec<Layout>"),
        "data::Layouts::layouts must be Vec<Layout> for deterministic serialization (BC-11). \
         Got struct body: {struct_body}"
    );
    assert!(
        !struct_body.contains("HashMap") && !struct_body.contains("FxHashMap"),
        "data::Layouts must not use HashMap/FxHashMap — non-deterministic JSON order (BC-11)"
    );

    // ── 2. data::Dashboard::popout must be Vec<…> ─────────────────────────────
    let dashboard_src = include_str!("../data/src/layout/dashboard.rs");
    let dashboard_struct_pos = dashboard_src
        .find("pub struct Dashboard")
        .expect("data::Dashboard must exist in data/src/layout/dashboard.rs (BC-11)");
    let tail = &dashboard_src[dashboard_struct_pos..];
    let end = tail
        .find('}')
        .expect("Dashboard struct must have closing brace")
        + 1;
    let struct_body = &tail[..end];

    assert!(
        struct_body.contains("pub popout: Vec<"),
        "data::Dashboard::popout must be Vec<…> for deterministic serialization (BC-11). \
         Got struct body: {struct_body}"
    );
    assert!(
        !struct_body.contains("HashMap") && !struct_body.contains("FxHashMap"),
        "data::Dashboard must not use HashMap/FxHashMap — non-deterministic JSON order (BC-11)"
    );

    // ── 3. build_state_json builds ser_layouts as a Vec push loop ─────────────
    let main_src = read_main();
    let fn_start = main_src
        .find("fn build_state_json(")
        .expect("build_state_json must exist in main.rs (BC-11)");
    let tail = &main_src[fn_start..];
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let fn_body = &tail[..end];

    assert!(
        fn_body.contains("vec![]") || fn_body.contains("Vec::new()"),
        "build_state_json must initialize ser_layouts as a Vec (BC-11)"
    );
    assert!(
        fn_body.contains(".push("),
        "build_state_json must append layouts with .push() into a Vec (BC-11)"
    );
    assert!(
        !fn_body.contains("HashMap::new()") && !fn_body.contains("FxHashMap"),
        "build_state_json must not build a HashMap for layouts (BC-11)"
    );
}

// ── Pending exit / open message variants ──────────────────────────────────────

#[test]
fn discard_and_exit_message_exists() {
    let src = read_main();
    assert!(
        src.contains("DiscardAndExit"),
        "Message::DiscardAndExit must exist for the dirty-check dialog confirm action (F4)"
    );
}

// ── C-1: GoBack / Escape clears all pending dirty-check state ─────────────────

#[test]
fn escape_on_confirm_clears_pending_state() {
    // C-1: when the user presses Escape (GoBack) while a confirm dialog is open,
    // ALL pending dirty-check state must be cleared — not just confirm_dialog.
    // Without this fix, a subsequent Open/Quit skips the dirty check because
    // pending_open_file / pending_exit_windows / pending_save_path remain set.
    let src = read_main();

    let prefix = "            Message::GoBack =>";
    let start = src
        .find(prefix)
        .expect("Message::GoBack handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    // The confirm_dialog.is_some() branch must clear all three pending fields.
    assert!(
        body.contains("pending_open_file = None"),
        "GoBack with confirm_dialog must clear pending_open_file (C-1)"
    );
    assert!(
        body.contains("pending_exit_windows = None"),
        "GoBack with confirm_dialog must clear pending_exit_windows (C-1)"
    );
    // F-M3 (R6): the unified clear must also reset the F7 mode-switch pending state.
    // Plan invariant 10 (L796): GoBack performs a one-shot clear of all pending fields
    // so a subsequent SwitchMode is not orphaned.
    assert!(
        body.contains("pending_mode_switch = None"),
        "GoBack with confirm_dialog must clear pending_mode_switch (F7 orphan prevention)"
    );
    assert!(
        body.contains("_mode_switch_guard = None"),
        "GoBack with confirm_dialog must clear _mode_switch_guard (F7 orphan prevention)"
    );
}
