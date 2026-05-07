//! H-1: `map_engine_event_to_message` exhaustive-coverage guard.
//!
//! Scans `src/main.rs` to verify that every `EngineEvent` variant is either:
//!   (a) handled by an explicit arm in `map_engine_event_to_message`, or
//!   (b) legitimately caught by the trailing `_ => None` wildcard.
//!
//! Additionally confirms that `EngineEvent::EngineError` has an **explicit** arm
//! (not silently swallowed by `_ => None`) so connection-level errors produce
//! a `log::error!` entry rather than disappearing.
//!
//! Pattern: source-scan (same technique as `engine_event_replay_data_loaded_routing.rs`).

const MAIN_RS: &str = include_str!("../src/main.rs");
const DTO_RS: &str = include_str!("../engine-client/src/dto.rs");

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

/// Returns the list of variant names declared in `EngineEvent`.
/// Reads from `engine-client/src/dto.rs` between the enum opening and
/// the first `fn` after it (the `fn default_true()` sentinel).
fn engine_event_variants() -> Vec<&'static str> {
    // Slice from enum open brace to the closing brace.
    // We know from inspection that the enum ends before `fn default_true`.
    let start = DTO_RS
        .find("pub enum EngineEvent")
        .expect("EngineEvent enum not found in dto.rs");
    let rest = &DTO_RS[start..];
    let sentinel = rest.find("\nfn default_true").unwrap_or(rest.len());
    let body = &rest[..sentinel];

    // Each variant starts with exactly 4 spaces followed by a capital letter.
    body.lines()
        .filter_map(|line| {
            let trimmed = line.trim_start();
            // Must be indented by exactly 4 spaces and start with uppercase.
            if line.starts_with("    ")
                && !line.starts_with("     ")
                && trimmed.starts_with(|c: char| c.is_ascii_uppercase())
                && !trimmed.starts_with("//")
                && !trimmed.starts_with('#')
            {
                // Take only the identifier part (stop at space, '{', ',').
                let name = trimmed
                    .split(|c: char| c == ' ' || c == '{' || c == ',')
                    .next()
                    .unwrap_or("");
                if name
                    .chars()
                    .next()
                    .map_or(false, |c| c.is_ascii_uppercase())
                {
                    Some(name)
                } else {
                    None
                }
            } else {
                None
            }
        })
        .collect()
}

// ── H-1a: EngineError arm exists and has explicit log::error! ────────────────

#[test]
fn engine_error_has_explicit_arm_in_dispatcher() {
    let body = extract_function_body(MAIN_RS, "fn map_engine_event_to_message")
        .expect("could not extract map_engine_event_to_message body");

    assert!(
        body.contains("EngineEvent::EngineError"),
        "map_engine_event_to_message must have an explicit arm for `EngineEvent::EngineError` \
         (H-1). Without it, connection-level errors silently fall into `_ => None` with no \
         log output. Add an arm that calls `log::error!` for the `strategy_id: None` case."
    );
}

#[test]
fn engine_error_connection_level_arm_logs_error() {
    let body = extract_function_body(MAIN_RS, "fn map_engine_event_to_message")
        .expect("could not extract map_engine_event_to_message body");

    // The arm for connection-level (strategy_id: None) should emit log::error!
    assert!(
        body.contains("log::error!") && body.contains("EngineError"),
        "map_engine_event_to_message EngineError arm must call `log::error!` for \
         connection-level errors (strategy_id: None). This is the only observable \
         diagnostic for auth_failed / schema_mismatch errors."
    );
}

// ── H-1b: high-value variants each have an explicit arm ──────────────────────

/// Variants that must never be silently dropped by `_ => None`.
/// Each has significant observable side-effects in the UI or logging.
const REQUIRED_EXPLICIT_ARMS: &[&str] = &[
    "EngineEvent::EngineError",
    "EngineEvent::EngineStopped",
    "EngineEvent::ReplayStopped",
    "EngineEvent::ReplayDataLoaded",
    "EngineEvent::EngineStarted",
    "EngineEvent::RestoreSnapshot",
    "EngineEvent::ReplayHistoryChanged",
    "EngineEvent::DateChangeMarker",
];

#[test]
fn required_high_value_arms_exist_in_dispatcher() {
    let body = extract_function_body(MAIN_RS, "fn map_engine_event_to_message")
        .expect("could not extract map_engine_event_to_message body");

    for arm in REQUIRED_EXPLICIT_ARMS {
        assert!(
            body.contains(arm),
            "map_engine_event_to_message is missing an explicit arm for `{arm}`. \
             This variant must not silently fall into `_ => None` because it has \
             UI-observable side-effects. Add an explicit arm (H-1b)."
        );
    }
}

// ── H-1c: enum variants listing is stable ────────────────────────────────────

#[test]
fn engine_event_variant_count_is_as_expected() {
    let variants = engine_event_variants();
    // We know the exact count from inspection of dto.rs (2026-05-08).
    // If this fails, a new variant was added — ensure it gets an explicit arm
    // or a deliberate comment explaining why `_ => None` is correct.
    let count = variants.len();
    // Exact count pinned 2026-05-08. If this fails: a variant was added or removed.
    // When adding a new EngineEvent variant, ALSO add an explicit arm in
    // `map_engine_event_to_message` (src/main.rs) — do NOT rely on `_ => None`.
    assert_eq!(
        count, 52,
        "EngineEvent variant count changed (got {count}, expected 52). \
         Ensure the new/removed variant has an explicit arm in map_engine_event_to_message."
    );
}
