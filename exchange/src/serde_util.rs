use serde::{Deserialize, Deserializer, de::Error as DeError};
use serde_json::Value;

/// Deserialize `f32` accepting either a JSON number or a stringified number.
///
/// Python workers serialize `daily_price_chg` via `str(...)` (see
/// `python/engine/exchanges/*.py`), so the IPC payload arrives as a JSON
/// string even though the Rust DTO stores it as `f32`. Without this helper
/// serde fails with `invalid type: string "...", expected f32`, silently
/// dropping the entire `TickerStats` entry and producing an empty sidebar.
pub fn de_f32_from_number_or_string<'de, D>(deserializer: D) -> Result<f32, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Value::deserialize(deserializer)?;
    match value {
        Value::Number(n) => n
            .as_f64()
            .map(|v| v as f32)
            .ok_or_else(|| D::Error::custom("expected finite f32")),
        Value::String(s) => s.parse::<f32>().map_err(D::Error::custom),
        other => Err(D::Error::custom(format!(
            "expected f32 as number or string, got {other:?}"
        ))),
    }
}

pub(crate) fn value_as_f32(value: &Value) -> Option<f32> {
    match value {
        Value::String(s) => s.parse::<f32>().ok(),
        Value::Number(n) => n.as_f64().map(|v| v as f32),
        _ => None,
    }
}

pub(crate) fn de_number_like_or_object<'de, D, T>(
    deserializer: D,
    expected_name: &'static str,
    from_f32: impl Fn(f32) -> T,
) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: serde::de::DeserializeOwned,
{
    let value = Value::deserialize(deserializer)?;

    match value {
        Value::Object(_) => serde_json::from_value::<T>(value).map_err(D::Error::custom),
        Value::String(s) => {
            let number = s.parse::<f32>().map_err(D::Error::custom)?;
            Ok(from_f32(number))
        }
        Value::Number(n) => {
            let number = n
                .as_f64()
                .map(|v| v as f32)
                .ok_or_else(|| D::Error::custom(format!("expected numeric {expected_name}")))?;
            Ok(from_f32(number))
        }
        _ => Err(D::Error::custom(format!(
            "expected {expected_name} as string or number"
        ))),
    }
}
