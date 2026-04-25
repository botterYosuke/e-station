//! Helpers for reading typed values out of the `Ready.capabilities` blob
//! (which stays untyped on the Rust side per F-M8 — Python is the schema
//! source of truth, Rust just probes specific paths).
//!
//! The contract is intentionally narrow: callers ask for one
//! `(venue, key)` at a time and get back a `Result<Option<T>, _>` so that
//! a missing field is distinguishable from a malformed one. Silent `false`
//! / `None` defaults at this layer are forbidden — they would let a typo
//! in either Python or Rust reach the UI undetected.

use serde::de::DeserializeOwned;
use serde_json::Value;

#[derive(Debug, thiserror::Error)]
pub enum CapabilityError {
    #[error("capabilities root is not an object")]
    RootNotObject,
    #[error("venue_capabilities is not an object")]
    VenueCapsNotObject,
    #[error("venue_capabilities[{venue}] is not an object")]
    VenueEntryNotObject { venue: String },
    #[error("venue_capabilities[{venue}].{key} could not be deserialized: {source}")]
    Deserialize {
        venue: String,
        key: String,
        #[source]
        source: serde_json::Error,
    },
}

/// Look up a typed value at `capabilities.venue_capabilities[<venue>][<key>]`.
///
/// - `Ok(None)` — the path simply isn't there (venue not declared, or the
///   specific capability key not declared for this venue).
/// - `Ok(Some(v))` — the value was present and parsed.
/// - `Err(_)` — the structure was malformed in a way that would mask bugs
///   if we silently returned `None`.
pub fn venue_capability<T>(
    capabilities: &Value,
    venue: &str,
    key: &str,
) -> Result<Option<T>, CapabilityError>
where
    T: DeserializeOwned,
{
    let root = capabilities
        .as_object()
        .ok_or(CapabilityError::RootNotObject)?;
    let venue_caps = match root.get("venue_capabilities") {
        Some(v) => v.as_object().ok_or(CapabilityError::VenueCapsNotObject)?,
        None => return Ok(None),
    };
    let entry = match venue_caps.get(venue) {
        Some(v) => v.as_object().ok_or(CapabilityError::VenueEntryNotObject {
            venue: venue.to_string(),
        })?,
        None => return Ok(None),
    };
    let value = match entry.get(key) {
        Some(v) => v,
        None => return Ok(None),
    };
    let parsed: T =
        serde_json::from_value(value.clone()).map_err(|source| CapabilityError::Deserialize {
            venue: venue.to_string(),
            key: key.to_string(),
            source,
        })?;
    Ok(Some(parsed))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn caps() -> Value {
        json!({
            "supported_venues": ["tachibana"],
            "venue_capabilities": {
                "tachibana": {
                    "supports_depth_diff": false,
                    "supported_timeframes": ["1d"]
                }
            }
        })
    }

    #[test]
    fn returns_value_when_present() {
        let caps = caps();
        let v: Option<bool> = venue_capability(&caps, "tachibana", "supports_depth_diff").unwrap();
        assert_eq!(v, Some(false));
    }

    #[test]
    fn returns_none_for_missing_key() {
        let caps = caps();
        let v: Option<bool> = venue_capability(&caps, "tachibana", "no_such").unwrap();
        assert!(v.is_none());
    }

    #[test]
    fn returns_none_for_missing_venue() {
        let caps = caps();
        let v: Option<Vec<String>> = venue_capability(&caps, "binance", "supported_timeframes")
            .unwrap();
        assert!(v.is_none());
    }

    #[test]
    fn returns_none_when_venue_capabilities_missing() {
        let caps = json!({"supported_venues": []});
        let v: Option<bool> = venue_capability(&caps, "tachibana", "x").unwrap();
        assert!(v.is_none());
    }

    #[test]
    fn errors_when_root_not_object() {
        let caps = json!("oops");
        let v: Result<Option<bool>, _> = venue_capability(&caps, "tachibana", "x");
        assert!(matches!(v, Err(CapabilityError::RootNotObject)));
    }

    #[test]
    fn errors_when_value_type_mismatches() {
        let caps = caps();
        let v: Result<Option<bool>, _> =
            venue_capability(&caps, "tachibana", "supported_timeframes");
        assert!(matches!(v, Err(CapabilityError::Deserialize { .. })));
    }
}
