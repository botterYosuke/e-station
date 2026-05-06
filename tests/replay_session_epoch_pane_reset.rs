//! schema 3.14: regression guard for the replay-file-switch stale-pane bug.
//!
//! See [docs/✅python-data-engine/🔵replay-file-switch-stale-panes-approach-b.md].
//!
//! The fix introduces a monotonically-increasing `session_epoch: Option<u64>`
//! on `ReplayDataLoaded`, plus a GUI-side `Flowsurface::last_replay_session_epoch`
//! tracker.  When the GUI observes a new epoch (`prev != curr`), it drains
//! every registered pane via `ReplayPaneRegistry::drain_all_registered()` and
//! closes them on the active dashboard before generating fresh panes for the
//! new session.
//!
//! Because `flowsurface` is a bin-only crate, the dispatcher and `update()`
//! cannot be called from an integration test binary.  We use the same
//! source-scan technique as `multiinst_replay_pane_routing.rs`.

const SOURCE: &str = include_str!("../src/main.rs");

fn extract_function_body<'a>(source: &'a str, sig_marker: &str) -> Option<&'a str> {
    let start = source.find(sig_marker)?;
    let rest = &source[start..];
    let open = rest.find('{')?;
    let bytes = rest.as_bytes();
    let mut depth: i32 = 0;
    let mut i = open;
    while i < bytes.len() {
        match bytes[i] {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(&rest[open..=i]);
                }
            }
            _ => {}
        }
        i += 1;
    }
    None
}

/// Window over the `Message::ReplayDataLoaded` arm in `update()` (the *handler*,
/// not the variant declaration nor the dispatcher).
fn handler_window() -> &'static str {
    let handler_start = SOURCE
        .rfind("Message::ReplayDataLoaded {")
        .expect("Message::ReplayDataLoaded arm in update() not found");
    let rest = &SOURCE[handler_start..];
    &rest[..rest.len().min(6_000)]
}

// 1. drain_all_registered unit logic — covered in
//    `src/screen/dashboard/replay_pane_registry.rs::tests`. The crate-internal
//    test exercises HashMap-clear and pane-id collection because `pane_grid::Pane`
//    has a `pub(super)` constructor unreachable from this binary.

// 2. Message::ReplayDataLoaded carries session_epoch.
#[test]
fn message_replay_data_loaded_has_session_epoch_field() {
    let variant_start = SOURCE
        .find("ReplayDataLoaded {")
        .expect("Message::ReplayDataLoaded variant not found in src/main.rs");
    let rest = &SOURCE[variant_start..];
    let end = rest
        .find('}')
        .expect("closing brace of ReplayDataLoaded variant not found");
    let body = &rest[..end];

    assert!(
        body.contains("session_epoch"),
        "Message::ReplayDataLoaded must declare `session_epoch: Option<u64>` \
         (schema 3.14). Without it the GUI cannot detect replay-file-switch \
         boundaries and the previous file's panes remain as zombies — the bug \
         this fix is for."
    );
    assert!(
        body.contains("Option<u64>"),
        "session_epoch must be `Option<u64>` so old engines (schema_minor<14) \
         that omit the field deserialise as `None`."
    );
}

// 3. Dispatcher forwards session_epoch from EngineEvent → Message.
#[test]
fn dispatcher_forwards_session_epoch_to_message() {
    let body = extract_function_body(SOURCE, "fn map_engine_event_to_message")
        .expect("could not extract map_engine_event_to_message body");

    assert!(
        body.contains("session_epoch"),
        "map_engine_event_to_message must destructure and forward `session_epoch` \
         from EngineEvent::ReplayDataLoaded to Message::ReplayDataLoaded. \
         Dropping it with `..` re-introduces the file-switch stale pane bug \
         (same failure mode as the schema 3.13 instrument_ids regression)."
    );
}

// 4. Flowsurface holds last_replay_session_epoch.
#[test]
fn flowsurface_has_last_replay_session_epoch_field() {
    // Match the field declaration line directly so we don't conflate this with
    // any incidental usage in comments / tests / println.
    assert!(
        SOURCE.contains("last_replay_session_epoch: Option<u64>"),
        "Flowsurface must declare `last_replay_session_epoch: Option<u64>` so \
         the handler can detect when the engine starts a new replay session."
    );
}

// 5. Handler compares last_replay_session_epoch against the incoming session_epoch.
#[test]
fn handler_compares_session_epoch_for_change_detection() {
    let window = handler_window();
    assert!(
        window.contains("last_replay_session_epoch")
            && window.contains("session_epoch"),
        "Message::ReplayDataLoaded handler must read `self.last_replay_session_epoch` \
         and compare it against the incoming `session_epoch` to detect session \
         boundaries."
    );
    assert!(
        window.contains("!=") || window.contains("prev != curr"),
        "Handler must use `!=` (not `>` / `==`) so engine restarts that roll the \
         epoch back to a smaller value still trigger a session reset."
    );
}

// 6. session_changed=true triggers drain_all_registered + panes.close.
#[test]
fn handler_closes_stale_panes_when_session_epoch_changes() {
    let window = handler_window();
    assert!(
        window.contains("drain_all_registered"),
        "Handler must call `replay_pane_registry.drain_all_registered()` when \
         the session epoch advances, so stale panes from the previous file are \
         removed."
    );
    assert!(
        window.contains("panes.close"),
        "Handler must call `dashboard.panes.close(pane)` on every drained pane \
         so the pane_grid actually drops the stale UI elements (drain alone \
         only resets the registry, not the visible grid)."
    );
}

// 7. Old-engine compat: persistent None must not trigger drain.
#[test]
fn handler_does_not_close_when_session_epoch_is_none() {
    let window = handler_window();
    // The match expression must include a fall-through that yields `false` for
    // the None cases. We check both the explicit pair and the catch-all.
    let has_none_arm = window.contains("(None, None)")
        || window.contains("_ => false")
        || window.contains("(_, None)");
    assert!(
        has_none_arm,
        "Handler's session-change match must yield `false` when `session_epoch` \
         is `None` (old engine schema_minor<14). Otherwise every legacy engine \
         response would clobber panes on every load."
    );
}

// 8. Same epoch repeated must not trigger drain.
#[test]
fn handler_does_not_close_for_same_session_epoch() {
    let window = handler_window();
    assert!(
        window.contains("prev != curr") || window.contains("!= curr"),
        "Handler must use a `prev != curr` comparison on `(Some(prev), Some(curr))` \
         so a duplicate ReplayDataLoaded with the same epoch (e.g. incremental \
         load future-extension or re-emit) does not destroy the live panes."
    );
}

// 9. Disconnect resets last_replay_session_epoch to None.
#[test]
fn disconnect_resets_last_replay_session_epoch() {
    // The reset lives in the EngineRestarting(true) branch — find that branch
    // and confirm `last_replay_session_epoch = None` is inside it.
    let restart_idx = SOURCE
        .find("Message::EngineRestarting(restarting)")
        .expect("Message::EngineRestarting handler not found");
    let rest = &SOURCE[restart_idx..];
    let window = &rest[..rest.len().min(4_000)];
    assert!(
        window.contains("last_replay_session_epoch = None"),
        "On engine restart/disconnect the handler must reset \
         `self.last_replay_session_epoch = None`. Without this, the next \
         ReplayDataLoaded after the engine comes back with epoch=1 will compare \
         against the pre-disconnect Some(N) and either falsely fire a reset \
         (wrong direction) or — if N==1 — skip the reset entirely."
    );
}
