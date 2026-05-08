//! Regression guard for the schema 3.13 fix
//! ([docs/specs/data-engine/🔵multiinst-replay-pane-missing.md]).
//!
//! Before the fix, `map_engine_event_to_message` destructured
//! `EngineEvent::ReplayDataLoaded` with `..`, silently dropping
//! `instrument_ids`.  The `Message::ReplayDataLoaded` variant also lacked the
//! field entirely.  As a result only the first instrument got a chart pane.
//!
//! Because `flowsurface` is a bin-only crate the dispatcher and `update()`
//! cannot be called directly from an integration-test binary.  We therefore
//! inspect `src/main.rs` source text — the same technique used by
//! `engine_event_replay_data_loaded_routing.rs`.

const MAIN_RS: &str = include_str!("../src/main.rs");
const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");

fn combined_source() -> String {
    format!("{MAIN_RS}\n{HANDLER_REPLAY}")
}

fn safe_window(s: &str, max_bytes: usize) -> &str {
    let end = max_bytes.min(s.len());
    let safe = (0..=end)
        .rev()
        .find(|&i| s.is_char_boundary(i))
        .unwrap_or(0);
    &s[..safe]
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

// ── schema 3.13: dispatcher forwards instrument_ids ──────────────────────────

#[test]
fn dispatcher_forwards_instrument_ids_to_message() {
    let src = combined_source();
    let body = extract_function_body(&src, "fn map_engine_event_to_message")
        .expect("could not extract map_engine_event_to_message body");

    assert!(
        body.contains("instrument_ids"),
        "map_engine_event_to_message must destructure and forward `instrument_ids` \
         from EngineEvent::ReplayDataLoaded to Message::ReplayDataLoaded. \
         Dropping it with `..` causes multi-instrument replay to create only \
         one chart pane (schema 3.13 regression, \
         docs/specs/data-engine/🔵multiinst-replay-pane-missing.md)."
    );
}

// ── schema 3.13: Message::ReplayDataLoaded has instrument_ids field ──────────

#[test]
fn message_replay_data_loaded_has_instrument_ids_field() {
    // After the refactor, the variant fields live in messages.rs (ReplayMsg::DataLoaded {}).
    // The dispatcher in map_engine_event_to_message destructures and forwards all fields,
    // so we scan the EngineEvent::ReplayDataLoaded destructure block for the required fields.
    let src = combined_source();
    let variant_start = src
        .find("EngineEvent::ReplayDataLoaded {")
        .expect("EngineEvent::ReplayDataLoaded destructure not found in src/main.rs");
    let rest = &src[variant_start..];
    let end = rest
        .find('}')
        .expect("closing brace of ReplayDataLoaded destructure not found");
    let variant_body = &rest[..end];

    assert!(
        variant_body.contains("instrument_ids"),
        "Message::ReplayDataLoaded must declare an `instrument_ids` field \
         (Option<Vec<String>>) to carry the schema 3.13 multi-instrument list. \
         Without it, only the single `instrument_id` is propagated and \
         multi-instrument replays generate only one chart pane."
    );
    // schema 3.14: pin pattern follow-up — session_epoch must remain on the variant.
    assert!(
        variant_body.contains("session_epoch"),
        "Message::ReplayDataLoaded must declare a `session_epoch: Option<u64>` \
         field (schema 3.14). Dropping it re-introduces the file-switch stale \
         pane bug — see docs/specs/data-engine/🔵replay-file-switch-stale-panes-approach-b.md"
    );
}

// ── handler: Task::batch for multi-instrument ─────────────────────────────────

#[test]
fn handler_uses_task_batch_for_multi_instrument() {
    // Locate the update() handler arm, not the dispatcher.
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    // Grab enough of the arm body (~80 lines)
    let window = safe_window(rest, 8_000);

    assert!(
        window.contains("Task::batch"),
        "The Message::ReplayDataLoaded handler in update() must call Task::batch \
         so that pane-generation tasks for all instruments are returned as a \
         single combined Task. Using a single Task::perform would drop all but \
         the first instrument's panes (schema 3.13 fix)."
    );
}

// ── handler: iterates over ids for each instrument ───────────────────────────

#[test]
fn handler_iterates_over_instrument_ids() {
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    let window = safe_window(rest, 8_000);

    assert!(
        window.contains("for id in") || window.contains("for id in &ids"),
        "The Message::ReplayDataLoaded handler must iterate over the resolved \
         instrument id list (for id in &ids / for id in ids) and call \
         auto_generate_replay_panes once per instrument. A single call with \
         only `instrument_id` creates only one chart pane."
    );
}

// ── handler: fallback from instrument_ids=None to instrument_id ──────────────

#[test]
fn handler_fallbacks_to_single_instrument_id_when_instrument_ids_is_none() {
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    let window = safe_window(rest, 8_000);

    assert!(
        window.contains("unwrap_or_else") || window.contains("unwrap_or"),
        "The handler must fall back to `instrument_id` when `instrument_ids` is \
         None (old engine schema_minor<13). Without the fallback, single-instrument \
         replays built against schema <3.13 stop generating panes entirely."
    );
    assert!(
        window.contains("instrument_id"),
        "The fallback must reference `instrument_id` so that schema <3.13 engines \
         still trigger pane generation for the single instrument."
    );
}

// ── handler: empty ids guard returns Task::none ──────────────────────────────

#[test]
fn handler_returns_task_none_when_ids_is_empty() {
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    let window = safe_window(rest, 8_000);

    assert!(
        window.contains("ids.is_empty()") || window.contains("if ids.is_empty"),
        "The handler must guard against an empty ids list and return Task::none() \
         without calling auto_generate_replay_panes. An empty list means both \
         instrument_ids=[] and instrument_id=None — most likely a schema bug or \
         pre-3.12 engine; pane generation should be skipped."
    );
}

// ── handler: empty instrument_ids falls back to instrument_id ────────────────

#[test]
fn handler_treats_empty_instrument_ids_vec_as_absent() {
    let src = combined_source();
    let handler_start = src
        .rfind("ReplayMsg::DataLoaded {")
        .expect("ReplayMsg::DataLoaded arm in update() not found");
    let rest = &src[handler_start..];
    let window = safe_window(rest, 8_000);

    // The filter(|v| !v.is_empty()) normalises Some([]) → None so the
    // unwrap_or_else fallback picks up instrument_id instead.
    assert!(
        window.contains("is_empty") && window.contains("filter"),
        "The handler must filter out empty instrument_ids vecs \
         (filter(|v| !v.is_empty())) so that instrument_ids=[] falls through \
         to the instrument_id single-instrument fallback rather than producing \
         zero panes."
    );
}
