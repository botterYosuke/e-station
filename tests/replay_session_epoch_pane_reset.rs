//! schema 3.14: regression guard for the replay-file-switch stale-pane bug.
//!
//! See [docs/specs/data-engine/🔵replay-file-switch-stale-panes-approach-b.md].
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
const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");
const HANDLER_ENGINE: &str = include_str!("../src/handlers/engine.rs");

fn combined_source() -> String {
    format!("{SOURCE}\n{HANDLER_REPLAY}\n{HANDLER_ENGINE}")
}

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

/// Window over the `ReplayMsg::DataLoaded` arm in the handler (the *handler*,
/// not the variant declaration nor the dispatcher).
fn handler_window() -> String {
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    let max = rest.len().min(6_000);
    let safe_max = (0..=max)
        .rev()
        .find(|&i| rest.is_char_boundary(i))
        .unwrap_or(0);
    rest[..safe_max].to_string()
}

// 1. drain_all_registered unit logic — covered in
//    `src/screen/dashboard/replay_pane_registry.rs::tests`. The crate-internal
//    test exercises HashMap-clear and pane-id collection because `pane_grid::Pane`
//    has a `pub(super)` constructor unreachable from this binary.

// 2. Message::ReplayDataLoaded carries session_epoch.
// After the refactor, the variant fields are defined in src/messages.rs as ReplayMsg::DataLoaded.
const MESSAGES_RS: &str = include_str!("../src/messages.rs");

#[test]
fn message_replay_data_loaded_has_session_epoch_field() {
    // The variant is now ReplayMsg::DataLoaded in src/messages.rs.
    let variant_start = MESSAGES_RS
        .find("DataLoaded {")
        .expect("ReplayMsg::DataLoaded variant not found in src/messages.rs");
    let rest = &MESSAGES_RS[variant_start..];
    let end = rest
        .find('}')
        .expect("closing brace of DataLoaded variant not found");
    let body = &rest[..end];

    assert!(
        body.contains("session_epoch"),
        "ReplayMsg::DataLoaded must declare `session_epoch: Option<u64>` \
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
        window.contains("last_replay_session_epoch") && window.contains("session_epoch"),
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

// 6b (review fix R1 HIGH-4): (None, Some(_)) arm uses has_registered_panes()
//     guard so the very first ReplayDataLoaded after startup does NOT drain
//     when the registry is empty (the normal path).
#[test]
fn handler_none_to_some_uses_has_registered_panes_guard() {
    let window = handler_window();
    assert!(
        window.contains("has_registered_panes"),
        "Handler's `(None, Some(_))` arm must check `has_registered_panes()` \
         so the very first epoch after startup or reconnect does not drain. \
         Without this, the first LoadReplayData triggers a no-op drain on an \
         empty grid, which is benign now but loses meaning if helper-attach \
         pre-populates the registry."
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

// 9b (review fix): session-level OrderList / BuyingPower panes are registered
//     in `replay_pane_registry` so `drain_all_registered()` actually closes
//     them on file switch — otherwise the new session generates fresh
//     OrderList/BuyingPower panes alongside the orphans from the previous file.
const DASHBOARD_SOURCE: &str = include_str!("../src/screen/dashboard.rs");

#[test]
fn session_level_order_list_pane_is_registered_for_drain() {
    // Locate the auto_generate_replay_panes function body and assert that the
    // OrderList block both creates a pane AND registers it with the empty
    // sentinel instrument id. Without registration the pane survives a
    // session reset and shows up duplicated in file 2.
    let fn_idx = DASHBOARD_SOURCE
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let body = &DASHBOARD_SOURCE[fn_idx..];
    let order_list_block = body
        .find("auto-generated REPLAY OrderList pane")
        .expect("OrderList generation block not found");
    let window = &body[order_list_block..body.len().min(order_list_block + 1_500)];
    assert!(
        window.contains("register_pane(\"\", \"OrderList\"")
            || window.contains(
                "register_pane(\n                    \"\",\n                    \"OrderList\""
            ),
        "Session-level REPLAY OrderList pane must be registered in \
         `replay_pane_registry` (key: instrument_id=\"\", kind=\"OrderList\") so \
         that `drain_all_registered()` on file switch closes it. Without this \
         registration the new session creates a second OrderList pane and the \
         old one stays as a zombie. See review feedback on Approach B fix."
    );
}

#[test]
fn session_level_buying_power_pane_is_registered_for_drain() {
    let fn_idx = DASHBOARD_SOURCE
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let body = &DASHBOARD_SOURCE[fn_idx..];
    let buying_power_block = body
        .find("auto-generated REPLAY BuyingPower pane")
        .expect("BuyingPower generation block not found");
    let window = &body[buying_power_block..body.len().min(buying_power_block + 1_500)];
    assert!(
        window.contains("register_pane(\"\", \"BuyingPower\"")
            || window.contains(
                "register_pane(\n                    \"\",\n                    \"BuyingPower\""
            ),
        "Session-level REPLAY BuyingPower pane must be registered in \
         `replay_pane_registry` (key: instrument_id=\"\", kind=\"BuyingPower\") \
         so `drain_all_registered()` closes it on file switch. Same failure \
         mode as the OrderList case."
    );
}

// 9. Disconnect resets last_replay_session_epoch to None.
#[test]
fn disconnect_resets_last_replay_session_epoch() {
    // The reset lives in the EngineRestarting(true) branch — find that branch
    // and confirm `last_replay_session_epoch = None` is inside it.
    let src = combined_source();
    let restart_idx = src
        .find("EngineMsg::Restarting(restarting)")
        .expect("EngineMsg::Restarting handler not found");
    let rest = &src[restart_idx..];
    let max = rest.len().min(4_000);
    let safe_max = (0..=max)
        .rev()
        .find(|&i| rest.is_char_boundary(i))
        .unwrap_or(0);
    let window = &rest[..safe_max];
    assert!(
        window.contains("last_replay_session_epoch = None"),
        "On engine restart/disconnect the handler must reset \
         `self.last_replay_session_epoch = None`. Without this, the next \
         ReplayDataLoaded after the engine comes back with epoch=1 will compare \
         against the pre-disconnect Some(N) and either falsely fire a reset \
         (wrong direction) or — if N==1 — skip the reset entirely."
    );
}
